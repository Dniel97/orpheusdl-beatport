import logging
import re
from typing import Optional

from datetime import datetime

from utils.models import *
from utils.models import AlbumInfo
from .beatport_api import BeatportApi, BeatportError

module_information = ModuleInformation(
    service_name="Beatport",
    module_supported_modes=ModuleModes.download | ModuleModes.covers,
    login_behaviour=ManualEnum.manual,
    session_settings={"username": "", "password": ""},
    session_storage_variables=["access_token", "refresh_token", "expires"],
    netlocation_constant="beatport",
    url_decoding=ManualEnum.manual,
    test_url="https://www.beatport.com/track/darkside/10844269"
)


class ModuleInterface:
    # noinspection PyTypeChecker
    def __init__(self, module_controller: ModuleController):
        self.exception = module_controller.module_error
        self.disable_subscription_check = module_controller.orpheus_options.disable_subscription_check
        self.oprinter = module_controller.printer_controller
        self.print = module_controller.printer_controller.oprint
        self.module_controller = module_controller
        self.cover_size = module_controller.orpheus_options.default_cover_options.resolution

        # MINIMUM-MEDIUM = 128kbit/s AAC, HIGH = 256kbit/s AAC, LOSSLESS-HIFI = FLAC 44.1/16
        self.quality_parse = {
            QualityEnum.MINIMUM: "medium",
            QualityEnum.LOW: "medium",
            QualityEnum.MEDIUM: "medium",
            QualityEnum.HIGH: "medium",
            QualityEnum.LOSSLESS: "medium",
            QualityEnum.HIFI: "medium",
            QualityEnum.ATMOS: "medium"
        }

        self.session = BeatportApi()
        session = {
            "access_token": module_controller.temporary_settings_controller.read("access_token"),
            "refresh_token": module_controller.temporary_settings_controller.read("refresh_token"),
            "expires": module_controller.temporary_settings_controller.read("expires")
        }

        self.session.set_session(session)

        username = module_controller.module_settings.get("username")
        password = module_controller.module_settings.get("password")

        has_credentials = bool(username) and bool(password)

        if not has_credentials:
            self.print("Beatport: No credentials provided, running in anonymous mode (search results are limited - login for more)")
            # Always fetch a fresh anonymous token for search since they are short-lived
            # or immediately invalidate after a certain duration despite their JWT exp
            self.session.get_anonymous_token()
            self._save_session()
            return

        if session["refresh_token"] is None:
            # old beatport version with cookies and no refresh token, trigger login manually
            session = self.login(username, password)

        if session["refresh_token"] is not None and datetime.now() > session["expires"]:
            # access token expired, get new refresh token
            self.refresh_login()

        try:
            self.valid_account()
        except Exception as e:
            # Subscription check failed (expired account, no Link, etc.).
            # Clear stored session and re-login with credentials - user may have new account.
            err_msg = str(e).lower()
            if "subscription" in err_msg or "link" in err_msg:
                self.module_controller.temporary_settings_controller.set("access_token", None)
                self.module_controller.temporary_settings_controller.set("refresh_token", None)
                self.module_controller.temporary_settings_controller.set("expires", None)
                self.login(username, password)
            else:
                raise

    def _save_session(self) -> dict:
        # save the new access_token, refresh_token and expires in the temporary settings
        self.module_controller.temporary_settings_controller.set("access_token", self.session.access_token)
        self.module_controller.temporary_settings_controller.set("refresh_token", self.session.refresh_token)
        self.module_controller.temporary_settings_controller.set("expires", self.session.expires)

        return {
            "access_token": self.session.access_token,
            "refresh_token": self.session.refresh_token,
            "expires": self.session.expires
        }

    def refresh_login(self):
        logging.debug(f"Beatport: access_token expired, getting a new one")

        # get a new access_token and refresh_token from the API
        refresh_data = self.session.refresh()
        if refresh_data:
            # Refresh failed (invalid_grant, expired, revoked, account changed, etc.).
            # Clear stored session and re-login with credentials from settings.
            # Handles: expired subscriptions, new accounts, password changes.
            self.module_controller.temporary_settings_controller.set("access_token", None)
            self.module_controller.temporary_settings_controller.set("refresh_token", None)
            self.module_controller.temporary_settings_controller.set("expires", None)
            self.login(self.module_controller.module_settings["username"],
                       self.module_controller.module_settings["password"])
            return

        self._save_session()
            
    def login(self, email: str, password: str):
        logging.debug(f"Beatport: no session found, login")
        
        # Check if credentials are provided
        if not email or not password:
            raise self.exception(
                "Beatport credentials are missing in settings.json. "
                "Please fill in: username, password. "
                "Use the OrpheusDL GUI Settings tab (Beatport) or edit config/settings.json directly."
            )
        
        login_data = self.session.auth(email, password)

        if login_data.get("error_description") is not None:
            error_desc = login_data.get("error_description")
            # Check for blank field errors and provide a better message
            if isinstance(error_desc, dict):
                if "username" in error_desc and "password" in error_desc:
                    if any("blank" in str(msg).lower() for msg in error_desc.get("username", [])) and \
                       any("blank" in str(msg).lower() for msg in error_desc.get("password", [])):
                        raise self.exception(
                            "Beatport credentials are missing in settings.json. "
                            "Please fill in: username, password. "
                            "Use the OrpheusDL GUI Settings tab (Beatport) or edit config/settings.json directly."
                        )
            raise self.exception(error_desc)

        self.valid_account()

        return self._save_session()

    def valid_account(self):
        if not self.disable_subscription_check:
            # get the subscription from the API and check if it's at least a "Link" subscription
            account_data = self.session.get_account()
            if not account_data.get("subscription"):
                raise self.exception("Beatport: Account does not have an active 'Link' subscription")

            # Essentials = "bp_basic", Professional = "bp_link_pro" (Monthly/Yearly/Annual)
            sub = account_data.get("subscription", "").lower()
            if sub == "bp_link_pro" or "pro" in sub:
                # Pro subscription, set the quality to high and lossless
                self.print("Beatport: Professional subscription detected, allowing high and lossless quality")
                self.quality_parse[QualityEnum.HIGH] = "high"
                self.quality_parse[QualityEnum.HIFI] = "lossless"
                self.quality_parse[QualityEnum.LOSSLESS] = "lossless"

    @staticmethod
    def custom_url_parse(link: str):
        # First, try to match library playlists (e.g., /library/playlists/6099487)
        # These don't have a slug, just the ID directly after the type
        library_match = re.search(r"https?://(www\.)?beatport\.com/(?P<region>[a-z]{2}/)?library/(?P<type>playlists)/(?P<id>\d+)", link)
        
        if library_match:
            # Extract region code if present
            region_code = library_match.group("region")
            if region_code:
                region_code = region_code.rstrip("/")
            
            extra_kwargs = {"is_chart": False}  # Library playlists are never charts
            if region_code:
                extra_kwargs["region"] = region_code
            
            return MediaIdentification(
                media_type=DownloadTypeEnum.playlist,
                media_id=library_match.group("id"),
                extra_kwargs=extra_kwargs
            )
        
        # Standard URL pattern with slug (e.g., /track/song-name/123, /release/album-name/456, /label/cenobite-records/33475)
        match = re.search(r"https?://(www\.)?beatport\.com/(?P<region>[a-z]{2}/)?(?P<type>track|release|artist|playlists|chart|label)/(?P<slug>.+)/(?P<id>\d+)", link)

        # so parse the regex "match" to the actual DownloadTypeEnum
        media_types = {
            "track": DownloadTypeEnum.track,
            "release": DownloadTypeEnum.album,
            "artist": DownloadTypeEnum.artist,
            "playlists": DownloadTypeEnum.playlist,
            "chart": DownloadTypeEnum.playlist,
            "label": DownloadTypeEnum.label
        }

        if not match: # Added error handling for robustness
            raise ValueError(f"Could not parse Beatport URL: {link}")

        # Extract region code if present (e.g., "es" from "/es/")
        region_code = match.group("region")
        if region_code:
            region_code = region_code.rstrip("/")  # Remove trailing slash

        extra_kwargs = {"is_chart": match.group("type") == "chart"}
        if match.group("type") == "label":
            extra_kwargs = {}
        if region_code:
            extra_kwargs["region"] = region_code

        return MediaIdentification(
            media_type=media_types[match.group("type")],
            media_id=match.group("id"),
            # check if the playlist is a user playlist or DJ charts, only needed for get_playlist_info()
            extra_kwargs=extra_kwargs
        )

    @staticmethod
    def _generate_artwork_url(cover_url: str, size: int, max_size: int = 1400):
        # Handle None or empty strings safely
        if not cover_url or not isinstance(cover_url, str):
            return None

        # if more than max_size are requested, cap the size at max_size
        if size > max_size:
            size = max_size

        # check if it"s a dynamic_uri, if not make it one
        res_pattern = re.compile(r"\d{3,4}x\d{3,4}")
        match = re.search(res_pattern, cover_url)
        if match:
            # replace the hardcoded resolution with dynamic one
            cover_url = re.sub(res_pattern, "{w}x{h}", cover_url)

        # replace the dynamic_uri h and w parameter with the wanted size
        try:
            return cover_url.format(w=size, h=size)
        except (KeyError, ValueError):
            return cover_url

    def search(self, query_type: DownloadTypeEnum, query: str, track_info: TrackInfo = None, limit: int = 20):
        # map query types to API search types
        search_types = {
            DownloadTypeEnum.track: "tracks",
            DownloadTypeEnum.album: "releases",
            DownloadTypeEnum.playlist: "charts",
            DownloadTypeEnum.artist: "artists",
            DownloadTypeEnum.label: "labels"
        }

        search_type = search_types.get(query_type)

        # Anonymous tokens 401 on categorized searches with 'type'
        if not self.session.refresh_token:
            search_type = None

        # perform search with type if supported, otherwise fall back to general search
        if search_type:
            if query_type is DownloadTypeEnum.playlist:
                # Fetch both charts and user playlists
                res_charts = self.session.get_search(query=query, search_type="charts", per_page=limit)
                res_playlists = self.session.get_search(query=query, search_type="playlists", per_page=limit)
                result_list = res_charts.get("charts", []) + res_playlists.get("playlists", [])
            else:
                results = self.session.get_search(query=query, search_type=search_type, per_page=limit)
                result_list = results.get(search_type, [])
        else:
            # fall back to general search for unsupported types
            results = self.session.get_search(query)
            name_parse = {
                "track": "tracks",
                "album": "releases",
                "playlist": "charts", # Compatibility: result_list will additionally include 'playlists' below
                "artist": "artists",
                "label": "labels"
            }
            result_list = results.get(name_parse.get(query_type.name), [])
            if query_type is DownloadTypeEnum.playlist:
                result_list += results.get("playlists", [])
        
        items = []
        for i in result_list:
            # Initialize fields for SearchResult
            name = i.get('name', '')
            artists = []
            year = None
            duration = None
            additional = []
            item_extra_kwargs = {}
            
            # Identify if it's a chart or a user playlist
            is_chart = 'publish_date' in i or 'person' in i or 'genres' in i
            item_extra_kwargs['is_chart'] = is_chart

            # Safe handling of image data - handle None values properly
            if query_type is DownloadTypeEnum.track:
                release_data = i.get('release') or {}
                image_data = release_data.get('image') or {}
            else:
                image_data = i.get('image') or {}
            image_uri = image_data.get('uri') or image_data.get('dynamic_uri') if isinstance(image_data, dict) else None
            
            # Beatport's default placeholder for artists - we want to replace this with a better one
            beatport_default_placeholder = "0dc61986-bccf-49d4-8fad-6b147ea8f327"
            beatport_preferred_placeholder = "https://geo-media.beatport.com/image_size/500x500/ab2d1d04-233d-4b08-8234-9782b34dcab8.jpg"
            
            # Check if the image is the default placeholder
            if image_uri and beatport_default_placeholder in image_uri:
                image_url = beatport_preferred_placeholder
            elif image_uri:
                image_url = self._generate_artwork_url(image_uri, 56)
            else:
                image_url = None
            
            # Fallback to preferred Beatport cover if no image available (for artists and labels)
            if not image_url and query_type in (DownloadTypeEnum.artist, DownloadTypeEnum.label):
                image_url = beatport_preferred_placeholder
            
            # Extract preview/sample URL (Beatport provides 2-minute previews)
            preview_url = i.get('sample_url') or i.get('preview_url') or i.get('sample', {}).get('url')
            
            result_id = str(i.get('id'))
            is_explicit = i.get('explicit', False)

            if query_type is DownloadTypeEnum.playlist:
                # Artist parsing
                if i.get('artist') and i['artist'].get('name'):
                    artists = [i['artist']['name']]
                elif i.get('person') and i['person'].get('owner_name'):
                    artists = [i['person']['owner_name']]
                else:
                    artists = ["Beatport"]
                # Year parsing
                if i.get("publish_date"):
                    year = i.get("publish_date")[:4]
                elif i.get("created_date"):
                    year = i.get("created_date")[:4]
                # Track count
                if i.get("track_count") is not None:
                    tc = i.get('track_count')
                    additional.append(f"1 track" if tc == 1 else f"{tc} tracks")

            elif query_type is DownloadTypeEnum.track:
                artists = [a.get("name") for a in i.get("artists", [])]
                if i.get("publish_date"):
                    year = i.get("publish_date")[:4]
                if i.get("length_ms"):
                    duration = i.get("length_ms") // 1000
                if i.get("bpm"):
                    additional.append(f"{i.get('bpm')} BPM")
                if i.get("mix_name") and name: # Add mix name to track name
                    name += f" ({i.get('mix_name')})"

            elif query_type is DownloadTypeEnum.album:
                artists = [a.get("name") for a in i.get("artists", [])]
                if i.get("publish_date"):
                    year = i.get("publish_date")[:4]
                if i.get("track_count") is not None:
                    tc = i.get('track_count'); additional.append(f"1 track" if tc == 1 else f"{tc} tracks")
                if i.get("catalog_number"):
                    additional.append(f"Cat: {i.get('catalog_number')}")
            
            elif query_type is DownloadTypeEnum.artist:
                if i.get("name"):
                    artists = [i.get("name")]
                if i.get("genres"):
                    genre_names = [g.get("name") for g in i.get("genres", []) if g.get("name")]
                    if genre_names:
                        additional.append(", ".join(genre_names))

            elif query_type is DownloadTypeEnum.label:
                rc = i.get("releases_count") or i.get("release_count")
                if rc is not None and rc == 0:
                    continue
                if i.get("name"):
                    artists = [i.get("name")]
                date_val = i.get("founded") or i.get("created_at") or i.get("founded_date")
                if date_val and isinstance(date_val, str) and len(date_val) >= 4:
                    year = date_val[:4]
                elif date_val and hasattr(date_val, 'year'):
                    year = str(getattr(date_val, 'year', ''))
                if i.get("genres"):
                    genre_names = [g.get("name") for g in i.get("genres", []) if g.get("name")]
                    if genre_names:
                        additional.append(", ".join(genre_names))
                if rc is not None:
                    additional.append(f"1 release" if rc == 1 else f"{rc} releases")

            if i.get("exclusive") is True:
                 additional.append("Exclusive")

            if query_type is DownloadTypeEnum.playlist and not is_chart and i.get('track_count') is None:
                # User playlists from search don't have track_count, we'll fetch it in batch
                pass
            elif query_type is DownloadTypeEnum.playlist and (i.get("track_count") is not None and i.get("track_count") == 0):
                continue

            item = SearchResult(
                name=name,
                artists=artists if artists else ["Unknown Artist"],
                result_id=result_id,
                year=year,
                additional=additional if additional else None,
                duration=duration,
                explicit=is_explicit,
                image_url=image_url,
                preview_url=preview_url,
                extra_kwargs=item_extra_kwargs if item_extra_kwargs else {}
            )
            items.append(item)

        # Batch fetch missing metadata for playlists (User playlists metadata and Chart durations)
        if query_type == DownloadTypeEnum.playlist:
            playlists_to_fetch = [t for t in items if not t.extra_kwargs.get('is_chart')]
            charts_to_fetch = [t for t in items if t.extra_kwargs.get('is_chart') and not t.duration]
            
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                # 1. Fetch full metadata for user playlists
                if playlists_to_fetch:
                    def _fetch_playlist_full(pid):
                        try:
                            data = self.session.get_playlist(pid)
                            return pid, data
                        except: return pid, None
                    
                    for pid, data in executor.map(_fetch_playlist_full, [t.result_id for t in playlists_to_fetch]):
                        if data:
                            for t in playlists_to_fetch:
                                if t.result_id == pid:
                                    # Update fields
                                    if data.get('track_count') is not None:
                                        t.additional = [f"1 track" if data['track_count'] == 1 else f"{data['track_count']} tracks"]
                                    if data.get('length_ms'):
                                        t.duration = data['length_ms'] // 1000
                                    
                                    # Safe access to release images
                                    rel_images = data.get('release_images')
                                    if not t.image_url and rel_images and len(rel_images) > 0 and rel_images[0]:
                                        t.image_url = self._generate_artwork_url(rel_images[0], 56)
                                    break
                
                # 2. Fetch durations for charts
                if charts_to_fetch:
                    def _fetch_chart_duration(pid):
                        try:
                            tracks_data = self.session.get_chart_tracks(pid, page=1, per_page=100)
                            if tracks_data and 'results' in tracks_data:
                                sum_dur = sum((t.get('length_ms') or 0) for t in tracks_data['results'])
                                return pid, sum_dur // 1000 if sum_dur > 0 else None
                        except: return pid, None
                    
                    for pid, dur in executor.map(_fetch_chart_duration, [t.result_id for t in charts_to_fetch]):
                        if dur:
                            for t in charts_to_fetch:
                                if t.result_id == pid:
                                    t.duration = dur
        
        # Batch fetch durations for albums
        albums_to_fetch = [t for t in items if query_type == DownloadTypeEnum.album and not t.duration]
        if albums_to_fetch:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                def _fetch_album_duration(aid):
                    try:
                        tracks_data = self.session.get_release_tracks(aid)
                        if tracks_data and 'results' in tracks_data:
                            sum_dur = sum((t.get('length_ms') or 0) for t in tracks_data['results'])
                            return aid, sum_dur // 1000 if sum_dur > 0 else None
                    except: return aid, None
                
                for aid, dur in executor.map(_fetch_album_duration, [t.result_id for t in albums_to_fetch]):
                    if dur:
                        for t in albums_to_fetch:
                            if t.result_id == aid:
                                t.duration = dur
                                break

        # 3. Final filtering: Remove any playlists that have no tracks
        if query_type == DownloadTypeEnum.playlist:
            # Helper to check for "X tracks" in additional list
            def _has_tracks(item):
                if not item.additional: return False
                # If there's an "X tracks" string, it has tracks
                return any("track" in str(s) for s in item.additional)
            
            items = [t for t in items if _has_tracks(t)]

        return items
        
    def get_playlist_info(self, playlist_id: str, is_chart: bool = False, **kwargs) -> PlaylistInfo:
        all_tracks_raw = []
        current_page = 1
        per_page = 100 # Max items per page Beatport API usually allows for tracks

        if is_chart:
            playlist_data = self.session.get_chart(playlist_id)
            # Initial fetch for chart tracks
            tracks_page_data = self.session.get_chart_tracks(playlist_id, page=current_page, per_page=per_page)
        else:
            playlist_data = self.session.get_playlist(playlist_id)
            # Initial fetch for playlist tracks
            tracks_page_data = self.session.get_playlist_tracks(playlist_id, page=current_page, per_page=per_page)

        if tracks_page_data and 'results' in tracks_page_data:
            all_tracks_raw.extend(tracks_page_data['results'])
        
        total_items = tracks_page_data.get('count', 0) if tracks_page_data else 0
        
        # Paginate if necessary
        while len(all_tracks_raw) < total_items and total_items > 0:
            current_page += 1
            self.print(f"Fetching playlist/chart tracks page {current_page} ({len(all_tracks_raw)}/{total_items})")
            if is_chart:
                tracks_page_data = self.session.get_chart_tracks(playlist_id, page=current_page, per_page=per_page)
            else:
                tracks_page_data = self.session.get_playlist_tracks(playlist_id, page=current_page, per_page=per_page)
            
            if tracks_page_data and 'results' in tracks_page_data and tracks_page_data['results']:
                all_tracks_raw.extend(tracks_page_data['results'])
            else:
                # No more results or error, break loop
                logging.warning(f"Stopped pagination for {'chart' if is_chart else 'playlist'} {playlist_id} at page {current_page}. Expected {total_items}, got {len(all_tracks_raw)}.")
                break
        if total_items > 0: self.print("") # Clear the progress line by printing a newline

        # For playlists (non-charts), tracks are often nested under a 'track' key.
        # For charts, the track data is usually direct.
        if not is_chart:
             processed_tracks_ids = [str(track_item['track']['id']) for track_item in all_tracks_raw if 'track' in track_item and 'id' in track_item['track']]
        else: # For charts
             processed_tracks_ids = [str(track_item['id']) for track_item in all_tracks_raw if 'id' in track_item]


        # Common fields for both charts and playlists
        name = playlist_data.get('name', 'Unknown Playlist')
        description = playlist_data.get('description', '')
        
        # Fields might differ between chart and playlist
        if is_chart:
            creator_name = playlist_data.get('curator_name')
            if not creator_name and playlist_data.get('artist'): # Charts might have an 'artist' as curator
                 creator_name = playlist_data.get('artist', {}).get('name', 'Beatport')
            elif not creator_name: # Fallback if no curator or artist name
                 creator_name = "Beatport"
            
            creator_id = str(playlist_data.get('artist', {}).get('id')) if playlist_data.get('artist') else None
            release_date_str = playlist_data.get('publish_date') # Charts use 'publish_date'
            image_data = playlist_data.get('image')
            num_tracks_from_api = playlist_data.get('track_count', len(processed_tracks_ids)) # Charts often have 'track_count'
            is_explicit = playlist_data.get('explicit', False)

        else: # For actual playlists (if distinct endpoint/structure exists and is used)
              # This part is more speculative as primary focus is charts based on search.
              # If Beatport API has distinct user playlists, structure might be like this:
            creator_name = playlist_data.get('user', {}).get('username', 'Unknown Creator') 
            creator_id = str(playlist_data.get('user', {}).get('id')) if playlist_data.get('user') else None
            release_date_str = playlist_data.get('created_at') # Or 'updated_at' for playlists
            image_data = playlist_data.get('image') # Structure might vary
            num_tracks_from_api = playlist_data.get('tracks_count', len(processed_tracks_ids)) # Or 'count'
            is_explicit = False # Playlists might not have a global explicit flag like albums/tracks.

        release_year = None
        if release_date_str:
            try:
                # Handle ISO format dates (e.g., "2023-04-01T15:00:00Z")
                release_year = datetime.fromisoformat(release_date_str.replace('Z', '+00:00')).year
            except ValueError:
                try: # Fallback for simpler date strings like "YYYY-MM-DD"
                    release_year = datetime.strptime(release_date_str.split('T')[0], '%Y-%m-%d').year
                except ValueError:
                    logging.warning(f"Could not parse release date for {'chart' if is_chart else 'playlist'} {playlist_id}: {release_date_str}")

        # Safe handling of image data - handle None values properly
        cover_uri = None
        if image_data and isinstance(image_data, dict):
            cover_uri = image_data.get('uri')
        cover_url = self._generate_artwork_url(cover_uri, self.cover_size) if cover_uri else None
        cover_type_str = 'jpg'
        if image_data and isinstance(image_data, dict) and image_data.get('extension'):
            cover_type_str = image_data.get('extension', 'jpg').lower()
        cover_type = ImageFileTypeEnum[cover_type_str] if cover_type_str in ImageFileTypeEnum.__members__ else ImageFileTypeEnum.jpg
        
        # Consistency check
        if num_tracks_from_api != len(processed_tracks_ids):
            logging.warning(f"Playlist/Chart {name} ({playlist_id}): Number of tracks from API ({num_tracks_from_api}) differs from successfully parsed tracks ({len(processed_tracks_ids)}).")

        # Calculate duration (sum of track durations if available, Beatport charts/playlists don't usually provide this directly)
        # This would require fetching individual track details, which is too slow here. So, duration remains None.
        total_duration_seconds = None

        return PlaylistInfo(
            name=name,
            creator=creator_name,
            creator_id=creator_id,
            description=description,
            duration=total_duration_seconds,
            release_year=release_year,
            cover_url=cover_url,
            cover_type=cover_type,
            tracks=processed_tracks_ids,
            explicit=is_explicit,
            track_extra_kwargs={'is_chart': is_chart} # Pass is_chart down for track processing
        )

    def get_artist_info(self, artist_id: str, get_credited_albums: bool, is_chart: bool = False, **kwargs) -> ArtistInfo:
        artist_data = self.session.get_artist(artist_id)
        artist_name = artist_data.get("name") or "Unknown Artist"

        # Fetch artist tracks (paginated)
        artist_tracks = []
        try:
            tracks_data = self.session.get_artist_tracks(artist_id)
            artist_tracks = list(tracks_data.get("results") or [])
            total_tracks = tracks_data.get("count") or len(artist_tracks)
            num_pages = max(1, (total_tracks + 99) // 100)
            for page in range(2, num_pages + 1):
                self.print(f"Fetching artist tracks (page {page}/{num_pages})...")
                artist_tracks += self.session.get_artist_tracks(artist_id, page=page).get("results") or []
        except Exception:
            pass

        # Fetch artist releases (paginated)
        releases_list = []
        try:
            releases_data = self.session.get_artist_releases(artist_id)
            releases_list = list(releases_data.get("results") or [])
            total_releases = releases_data.get("count") or len(releases_list)
            num_pages = max(1, (total_releases + 99) // 100)
            for page in range(2, num_pages + 1):
                self.print(f"Fetching artist releases (page {page}/{num_pages})...")
                releases_list += self.session.get_artist_releases(artist_id, page=page, per_page=100).get("results") or []
        except Exception:
            pass

        # Process releases into album dicts for GUI
        albums_out = []
        for r in releases_list:
            rid = str(r.get("id"))
            # Beatport releases usually have track_count but not duration in discography list
            tc = r.get("track_count")
            albums_out.append({
                'id': rid,
                'name': r.get("name"),
                'artist': artist_name,
                'release_year': str(r.get("new_release_date", ""))[:4] or None,
                'cover_url': r.get("image", {}).get("uri"),
                'additional': [f"1 track" if tc == 1 else f"{tc} tracks"] if tc else None,
                'duration': None # Will fetch via batch
            })

        # Batch fetch missing durations for albums
        missing_durations = [idx for idx, a in enumerate(albums_out) if a.get('duration') is None]
        if missing_durations:
            import concurrent.futures
            def _fetch_bp_album_duration(aid):
                try:
                    # We need to fetch release tracks to sum durations
                    r_tracks = self.session.get_release_tracks(aid).get("results") or []
                    total_sec = sum(int(float(t.get("length_ms", 0)) / 1000) for t in r_tracks)
                    return aid, total_sec or None
                except: return aid, None

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                fetch_ids = [albums_out[idx]['id'] for idx in missing_durations]
                for aid, dur in executor.map(_fetch_bp_album_duration, fetch_ids):
                    if dur:
                        for idx in missing_durations:
                            if albums_out[idx]['id'] == aid:
                                albums_out[idx]['duration'] = dur
                                break

        return ArtistInfo(
            name=artist_name,
            artist_id=artist_id,
            albums=albums_out,
            tracks=[t.get("id") for t in artist_tracks],
            track_extra_kwargs={"data": {t.get("id"): t for t in artist_tracks}},
        )

    def get_label_info(self, label_id: str, get_credited_albums: bool = True, **kwargs) -> ArtistInfo:
        """Return label metadata, releases (as albums), and tracks as ArtistInfo for consistent download flow."""
        label_data = self.session.get_label(label_id)
        label_name = label_data.get("name") or "Unknown Label"

        # Fetch all label tracks (paginated); API may not support this endpoint
        label_tracks = []
        try:
            tracks_data = self.session.get_label_tracks(label_id)
            label_tracks = list(tracks_data.get("results") or [])
            total_tracks = tracks_data.get("count") or len(label_tracks)
            num_pages = max(1, (total_tracks + 99) // 100)
            for page in range(2, num_pages + 1):
                self.print(f"Fetching label tracks (page {page}/{num_pages})...")
                label_tracks += self.session.get_label_tracks(label_id, page=page, per_page=100).get("results") or []
            if num_pages > 1:
                self.print("")
        except Exception:
            pass

        # Fetch all label releases (paginated)
        releases_list = []
        try:
            releases_data = self.session.get_label_releases(label_id)
            releases_list = list(releases_data.get("results") or [])
            total_releases = releases_data.get("count") or len(releases_list)
            num_pages = max(1, (total_releases + 99) // 100)
            for page in range(2, num_pages + 1):
                self.print(f"Fetching label releases (page {page}/{num_pages})...")
                releases_list += self.session.get_label_releases(label_id, page=page, per_page=100).get("results") or []
            if num_pages > 1:
                self.print("")
        except Exception:
            pass

        release_ids = [str(r.get("id")) for r in releases_list if r.get("id") is not None]
        track_ids = [t.get("id") for t in label_tracks if t.get("id") is not None]

        # Batch fetch missing durations for albums (cap to 100 to prevent API rate limits/hanging)
        missing_durations = [idx for idx, r in enumerate(releases_list) if r.get('duration') is None and r.get('length_ms') is None]
        missing_durations = missing_durations[:100]
        if missing_durations:
            import concurrent.futures
            def _fetch_bp_album_duration(aid):
                try:
                    r_tracks = self.session.get_release_tracks(aid).get("results") or []
                    total_sec = sum(int(float(t.get("length_ms", 0)) / 1000) for t in r_tracks)
                    return aid, total_sec or None
                except: return aid, None

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                fetch_ids = [str(releases_list[idx]['id']) for idx in missing_durations]
                future_to_idx = {executor.submit(_fetch_bp_album_duration, fetch_ids[i]): missing_durations[i] for i in range(len(fetch_ids))}
                completed = 0
                total = len(future_to_idx)
                for future in concurrent.futures.as_completed(future_to_idx):
                    completed += 1
                    if completed % 10 == 0 or completed == total:
                        self.print(f"Fetching release durations (page {completed}/{total})...")
                    try:
                        aid, dur = future.result()
                        if dur:
                            idx = future_to_idx[future]
                            releases_list[idx]['duration'] = dur
                    except Exception:
                        pass

        album_data = {str(r.get("id")): r for r in releases_list if r.get("id") is not None}
        track_data = {t.get("id"): t for t in label_tracks if t.get("id") is not None}

        return ArtistInfo(
            name=label_name,
            artist_id=label_id,
            albums=release_ids,
            album_extra_kwargs={"data": album_data},
            tracks=track_ids,
            track_extra_kwargs={"data": track_data},
        )

    def get_album_info(self, album_id: str, data=None, is_chart: bool = False, **kwargs) -> Optional[AlbumInfo]:
        # check if album is already in album cache, add it
        if data is None:
            data = {}

        try:
            album_data = data.get(album_id) if album_id in data else self.session.get_release(album_id)
        except BeatportError as e:
            error_message = str(e)
            import logging
            logging.warning(f"Beatport: Error getting album {album_id}: {error_message}")
            
            # Check the actual error - if it says "region locked" but the track is available on website,
            # it's probably NOT a real region lock but an API issue
            if "region locked" in error_message.lower():
                # Try workaround: attempt to get tracks directly even if release metadata fails
                # This might work if only the release endpoint has territory restrictions but tracks don't
                self.print(f"Beatport: Album {album_id} - API returned 'region locked' error for release metadata")
                self.print(f"  Attempting workaround: trying to get tracks directly...")
                try:
                    tracks_data = self.session.get_release_tracks(album_id)
                    tracks = tracks_data.get("results", [])
                    total_tracks = tracks_data.get("count", len(tracks))
                    
                    # If we can get tracks, try to get release info from first track
                    if tracks and len(tracks) > 0:
                        first_track = tracks[0]
                        release_data_from_track = first_track.get("release", {})
                        if release_data_from_track:
                            # Use release data from track instead
                            album_data = release_data_from_track
                            self.print(f"  Workaround successful: Got {total_tracks} tracks, using release info from track data")
                        else:
                            raise BeatportError("Could not get release info from tracks")
                    else:
                        raise BeatportError("No tracks found")
                except BeatportError as track_error:
                    # Workaround failed, show helpful error message
                    self.print(f"  Workaround failed: {str(track_error)}")
                    self.print(f"  Note: If this album is available on the Beatport website, this is likely an API issue, not a real region lock.")
                    self.print(f"  The Beatport API is returning 'Territory Restricted.' even though the content is available on the website.")
                    self.print(f"  This appears to be a bug in the Beatport API. You may need to:")
                    self.print(f"    1. Contact Beatport support about this API inconsistency")
                    self.print(f"    2. Try downloading individual tracks if they are available")
                    error_message = f"API error (Beatport API bug - false region lock): {error_message}. The album is available on the website but the API incorrectly reports it as territory restricted."
                    self.print(f"Beatport: Album {album_id} - {error_message}")
                    return
            elif "access denied" in error_message.lower() or "api error" in error_message.lower():
                error_message = f"API error: {error_message}. This might be a temporary API issue. Try again later or check your subscription status."
                self.print(f"Beatport: Album {album_id} - {error_message}")
            elif "not found" in error_message.lower():
                self.print(f"Beatport: Release {album_id} - This release does not exist (404). The URL may be invalid or the content may have been removed.")
            else:
                self.print(f"Beatport: Album {album_id} - {error_message}")
            return

        try:
            tracks_data = self.session.get_release_tracks(album_id)
        except BeatportError as e:
            error_message = str(e)
            import logging
            logging.warning(f"Beatport: Error getting album tracks for {album_id}: {error_message}")
            self.print(f"Beatport: Could not get tracks for album {album_id} - {error_message}")
            # Return album info without tracks if we can't get track list
            return AlbumInfo(
                name=album_data.get("name", "Unknown Album"),
                release_year=album_data.get("publish_date")[:4] if album_data.get("publish_date") else None,
                duration=0,
                upc=album_data.get("upc"),
                cover_url=self._generate_artwork_url(
                    (album_data.get("image") or {}).get("dynamic_uri"), self.cover_size) if album_data.get("image") else None,
                artist=album_data.get("artists")[0].get("name") if album_data.get("artists") else "Unknown Artist",
                artist_id=str(album_data.get("artists")[0].get("id")) if album_data.get("artists") else "",
                tracks=[],
                track_extra_kwargs={},
            )

        # now fetch all the found total_items
        tracks = tracks_data.get("results")
        total_tracks = tracks_data.get("count")
        num_pages = max(1, (total_tracks + 99) // 100)
        for page in range(2, num_pages + 1):
            print(f"Fetching {len(tracks)}/{total_tracks}", end="\r")
            tracks += self.session.get_release_tracks(album_id, page=page).get("results")

        cache = {"data": {album_id: album_data}}
        for i, track in enumerate(tracks):
            # add the track numbers
            track["number"] = i + 1
            # add the modified track to the track_extra_kwargs
            cache["data"][track.get("id")] = track

        return AlbumInfo(
            name=album_data.get("name"),
            release_year=album_data.get("publish_date")[:4] if album_data.get("publish_date") else None,
            # sum up all the individual track lengths
            duration=sum([(t.get("length_ms") or 0) // 1000 for t in tracks]),
            upc=album_data.get("upc"),
            cover_url=self._generate_artwork_url(
                (album_data.get("image") or {}).get("dynamic_uri"), self.cover_size) if album_data.get("image") else None,
            artist=album_data.get("artists")[0].get("name"),
            artist_id=album_data.get("artists")[0].get("id"),
            album_artist=album_data.get("artists")[0].get("name"),
            label=(album_data.get("label") or {}).get("name"),
            catalog_number=album_data.get("catalog_number"),
            tracks=[t.get("id") for t in tracks],
            track_extra_kwargs=cache,
        )

    def get_track_info(self, track_id: str, quality_tier: QualityEnum, codec_options: CodecOptions, slug: str = None,
                       data=None, is_chart: bool = False, **kwargs) -> TrackInfo:
        if data is None:
            data = {}

        try:
            # Support both str and int keys (artist/playlist track_data often has int ids from API)
            track_data = data.get(track_id) or (data.get(int(track_id)) if isinstance(track_id, str) and track_id.isdigit() else None)
            if track_data is None:
                track_data = self.session.get_track(track_id)
        except BeatportError as e:
            # Handle Beatport-specific errors gracefully
            error_message = str(e)
            import logging
            logging.warning(f"Beatport: Error getting track {track_id}: {error_message}")
            
            # Check if it's a "not found" error - this might mean the track ID doesn't match the API
            if "not found" in error_message.lower() or "no track matches" in error_message.lower():
                # Track not found - this could mean:
                # 1. The track ID in the URL doesn't match the API track ID
                # 2. The track is not available via the API endpoint (but might be on the website)
                # 3. The API has an issue
                # Print a helpful message to console
                self.print(f"Beatport: Track ID {track_id} not found in API.")
                self.print(f"  This can happen if the track ID in the URL doesn't match the API track ID.")
                self.print(f"  Solution: Use the release URL instead (e.g., https://www.beatport.com/release/.../RELEASE_ID)")
                error_message = f"Track not found in API: {error_message}. The track ID from the URL ({track_id}) might not match the API track ID. Try using the release URL instead."
            elif "region locked" in error_message.lower():
                error_message = f"Track is not available in your region. Original error: {error_message}"
            elif "subscription required" in error_message.lower():
                error_message = "Track requires a higher subscription level"
            elif "content not available" in error_message.lower():
                error_message = "Track is not available for download"
            elif "access denied" in error_message.lower() or "api error" in error_message.lower():
                # For access denied or API errors, show the full message for debugging
                error_message = f"API error: {error_message}. This might be a temporary API issue. Try again later or check your subscription status."
            
            # Return a minimal TrackInfo with error instead of crashing
            return TrackInfo(
                name="Unknown Track",
                album_id="",
                album="Unknown Album", 
                artists=["Unknown Artist"],
                artist_id="",
                bit_depth=16,
                bitrate=320,
                sample_rate=44.1,
                release_year=0,
                explicit=False,
                cover_url=None,
                tags=Tags(),                
                duration=None,
                codec=CodecEnum.AAC,  # Default codec for error cases
                error=error_message
            )

        # Safe access to release.id
        release_data = track_data.get("release") or {}
        album_id = release_data.get("id")
        album_data = {}
        error = None

        # When track_data came from cache (e.g. artist tracks), use embedded release to avoid N get_release calls
        if release_data and release_data.get("name") is not None and release_data.get("id") is not None:
            album_data = release_data
        try:
            if not album_data:
                album_data = data.get(album_id) or (data.get(int(album_id)) if album_id is not None and str(album_id).isdigit() else None)
            if not album_data:
                album_data = self.session.get_release(album_id) if album_id else {}
        except ConnectionError as e:
            # Log the actual error for debugging
            import logging
            logging.warning(f"Beatport: ConnectionError getting album {album_id}: {str(e)}")
            # Only mark as region locked if explicitly stated
            error_str = str(e)
            if "Territory Restricted." in error_str or "territory restricted" in error_str.lower():
                error = f"Album {album_id} is region locked"
            else:
                # Don't assume region lock - show the actual error
                error = f"Album {album_id} - {error_str}"

        track_name = track_data.get("name")
        track_name += f" ({track_data.get('mix_name')})" if track_data.get("mix_name") else ""

        release_year = track_data.get("publish_date")[:4] if track_data.get("publish_date") else None
        # Safe access to genre names
        genre_data = track_data.get("genre") or {}
        genres = [genre_data.get("name")] if genre_data.get("name") else []
        # check if a second genre exists
        sub_genre_data = track_data.get("sub_genre") or {}
        if sub_genre_data.get("name"):
            genres.append(sub_genre_data.get("name"))

        extra_tags = {}
        if track_data.get("bpm"):
            extra_tags["BPM"] = str(track_data.get("bpm"))
        key_data = track_data.get("key") or {}
        if key_data.get("name"):
            extra_tags["Key"] = key_data.get("name")
        if track_data.get("catalog_number"):
            extra_tags["Catalog number"] = track_data.get("catalog_number")

        # Safe access to nested release data
        label_data = release_data.get("label") or {}
        tags = Tags(
            album_artist=album_data.get("artists", [{}])[0].get("name"),
            track_number=track_data.get("number"),
            total_tracks=album_data.get("track_count"),
            upc=album_data.get("upc"),
            isrc=track_data.get("isrc"),
            genres=genres,
            release_date=track_data.get("publish_date"),
            copyright=f"© {release_year} {label_data.get('name')}" if label_data.get('name') else None,
            label=label_data.get("name"),
            catalog_number=track_data.get("catalog_number"),
            extra_tags=extra_tags
        )

        if not track_data["is_available_for_streaming"]:
            error = f"Track '{track_data.get('name')}' is not streamable!"
        elif track_data.get("preorder"):
            error = f"Track '{track_data.get('name')}' is not yet released!"

        quality = self.quality_parse[quality_tier]
        bitrate = {
            "lossless": 1411,
            "high": 256,
            "medium": 128,
        }
        length_ms = track_data.get("length_ms")

        # Safe access to release image data
        release_image_data = release_data.get("image") or {}
        cover_dynamic_uri = release_image_data.get("dynamic_uri")

        # Extract preview/sample URL (same as search; enables album track list preview in GUI)
        preview_url = track_data.get('sample_url') or track_data.get('preview_url') or (track_data.get('sample') or {}).get('url')

        track_info = TrackInfo(
            name=track_name,
            album=album_data.get("name"),
            album_id=album_data.get("id"),
            artists=[a.get("name") for a in track_data.get("artists")],
            artist_id=track_data.get("artists")[0].get("id"),
            id=str(track_id),
            release_year=release_year,
            duration=length_ms // 1000 if length_ms else None,
            bitrate=bitrate[quality],
            bit_depth=16 if quality == "lossless" else None,  # https://en.wikipedia.org/wiki/Audio_bit_depth#cite_ref-1
            sample_rate=44.1,
            cover_url=self._generate_artwork_url(cover_dynamic_uri, self.cover_size) if cover_dynamic_uri else None,
            tags=tags,
            codec=CodecEnum.FLAC if quality == "lossless" else CodecEnum.AAC,
            download_extra_kwargs={"track_id": track_id, "quality_tier": quality_tier},
            error=error,
            preview_url=preview_url,
            additional=f"{track_data.get('bpm')} BPM" if track_data.get("bpm") else None
        )

        return track_info

    def get_track_cover(self, track_id: str, cover_options: CoverOptions, data=None) -> CoverInfo:
        if data is None:
            data = {}

        track_data = data[track_id] if track_id in data else self.session.get_track(track_id)
        # Safe access to release image data
        release_data = track_data.get("release") or {}
        release_image_data = release_data.get("image") or {}
        cover_url = release_image_data.get("dynamic_uri")

        return CoverInfo(
            url=self._generate_artwork_url(cover_url, cover_options.resolution) if cover_url else None,
            file_type=ImageFileTypeEnum.jpg)

    def get_track_download(self, track_id: str, quality_tier: QualityEnum) -> TrackDownloadInfo:
        if not self.module_controller.module_settings.get("username") or not self.module_controller.module_settings.get("password"):
            raise self.exception("Downloading tracks requires a logged-in Beatport account. Please add your credentials in the settings.")

        stream_data = self.session.get_track_download(track_id, self.quality_parse[quality_tier])

        if not stream_data.get("location"):
            raise self.exception("Could not get stream, exiting")

        # Validate the download URL by checking content headers
        try:
            response = self.session.s.head(stream_data.get("location"), timeout=10)
            content_length = response.headers.get('content-length')
            content_type = response.headers.get('content-type', '')
            
            # Check if the content is suspiciously small (less than 1KB suggests corruption)
            if content_length and int(content_length) < 1024:
                raise self.exception(f"Track '{track_id}' appears to be corrupted (only {content_length} bytes available)")
            
            # Check if content type is appropriate for audio
            if content_type and not any(audio_type in content_type.lower() 
                                       for audio_type in ['audio', 'octet-stream', 'mpeg', 'flac', 'application', 'video', 'mp4']):
                raise self.exception(f"Track '{track_id}' does not contain valid audio content")
                
        except Exception as e:
            # If validation fails with an exception, assume the track is not available
            if hasattr(e, 'response') and e.response.status_code == 403:
                raise self.exception(f"Track '{track_id}' is not available for download (access denied)")
            elif "corrupted" in str(e) or "does not contain valid audio" in str(e):
                # Re-raise our own validation errors
                raise e
            else:
                # For other errors, log a warning but allow the download to proceed
                logging.warning(f"Could not validate download URL for track {track_id}: {e}")

        return TrackDownloadInfo(
            download_type=DownloadEnum.URL,
            file_url=stream_data.get("location")
        )