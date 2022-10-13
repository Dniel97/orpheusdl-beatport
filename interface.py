import logging
import re
import shutil
import ffmpeg

from datetime import datetime

from utils.models import *
from utils.utils import create_temp_filename
from .beatport_api import BeatportApi

module_information = ModuleInformation(
    service_name='Beatport',
    module_supported_modes=ModuleModes.download | ModuleModes.covers,
    session_settings={'username': '', 'password': ''},
    session_storage_variables=['access_token', 'refresh_token', 'expires'],
    netlocation_constant='beatport',
    url_decoding=ManualEnum.manual,
    test_url='https://www.beatport.com/track/darkside/10844269'
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

        # LOW = 128kbit/s AAC, MEDIUM = 128kbit/s AAC, HIGH = 256kbit/s AAC,
        self.quality_parse = {
            QualityEnum.MINIMUM: 128,
            QualityEnum.LOW: 128,
            QualityEnum.MEDIUM: 128,
            QualityEnum.HIGH: 256,
            QualityEnum.LOSSLESS: 256,
            QualityEnum.HIFI: 256
        }

        self.session = BeatportApi()
        session = {
            'access_token': module_controller.temporary_settings_controller.read('access_token'),
            'refresh_token': module_controller.temporary_settings_controller.read('refresh_token'),
            'expires': module_controller.temporary_settings_controller.read('expires')
        }

        self.session.set_session(session)

        if session['refresh_token'] is None:
            # old beatport version with cookies and no refresh token, trigger login manually
            self.login(module_controller.module_settings['username'], module_controller.module_settings['password'])

        if session['refresh_token'] is not None and datetime.now() > session['expires']:
            # access token expired, get new refresh token
            self.refresh_token()

        self.valid_account()

    def refresh_token(self):
        logging.debug(f'Beatport: access_token expired, getting a new one')

        # get a new access_token and refresh_token from the API
        refresh_data = self.session.refresh()
        if refresh_data and refresh_data.get('error') == 'invalid_grant':
            # if the refresh token is invalid, trigger login
            self.login(self.module_controller.module_settings['username'],
                       self.module_controller.module_settings['password'])
            return

        # save the new access_token, refresh_token and expires in the temporary settings
        self.module_controller.temporary_settings_controller.set('access_token', self.session.access_token)
        self.module_controller.temporary_settings_controller.set('refresh_token', self.session.refresh_token)
        self.module_controller.temporary_settings_controller.set('expires', self.session.expires)
            
    def login(self, email: str, password: str):
        logging.debug(f'Beatport: no session found, login')
        login_data = self.session.auth(email, password)

        if login_data.get('error_description') is not None:
            raise self.exception(login_data.get('error_description'))

        # save the new access_token, refresh_token and expires in the temporary settings
        self.module_controller.temporary_settings_controller.set('access_token', self.session.access_token)
        self.module_controller.temporary_settings_controller.set('refresh_token', self.session.refresh_token)
        self.module_controller.temporary_settings_controller.set('expires', self.session.expires)

        self.valid_account()

    def valid_account(self):
        if not self.disable_subscription_check:
            # get the subscription from the API and check if it's at least a "Link" subscription
            account_data = self.session.get_account()
            if not account_data.get('subscription'):
                raise self.exception('Beatport: Account does not have an active "Link" subscription')

    @staticmethod
    def custom_url_parse(link: str):
        match = re.search(r"https?://(www.)?beatport.com/(?P<type>track|release|artist|playlists|chart)/"
                          r".+?/(?P<id>\d+)", link)

        # so parse the regex "match" to the actual DownloadTypeEnum
        media_types = {
            'track': DownloadTypeEnum.track,
            'release': DownloadTypeEnum.album,
            'artist': DownloadTypeEnum.artist,
            'playlists': DownloadTypeEnum.playlist,
            'chart': DownloadTypeEnum.playlist
        }

        return MediaIdentification(
            media_type=media_types[match.group('type')],
            media_id=match.group('id'),
            # check if the playlist is a user playlist or DJ charts, only needed for get_playlist_info()
            extra_kwargs={'is_chart': match.group('type') == 'chart'}
        )

    @staticmethod
    def _generate_artwork_url(cover_url: str, size: int, max_size: int = 1400):
        # if more than max_size are requested, cap the size at max_size
        if size > max_size:
            size = max_size

        # check if it's a dynamic_uri, if not make it one
        res_pattern = re.compile(r'\d{3,4}x\d{3,4}')
        match = re.search(res_pattern, cover_url)
        if match:
            # replace the hardcoded resolution with dynamic one
            cover_url = re.sub(res_pattern, '{w}x{h}', cover_url)

        # replace the dynamic_uri h and w parameter with the wanted size
        return cover_url.format(w=size, h=size)

    def search(self, query_type: DownloadTypeEnum, query: str, track_info: TrackInfo = None, limit: int = 20):
        results = self.session.get_search(query)

        name_parse = {
            'track': 'tracks',
            'album': 'releases',
            'playlist': 'charts',
            'artist': 'artists'
        }

        items = []
        for i in results.get(name_parse.get(query_type.name)):
            additional = []
            duration = None
            if query_type is DownloadTypeEnum.playlist:
                artists = [i.get('person').get('owner_name') if i.get('person') else 'Beatport']
                year = i.get('change_date')[:4] if i.get('change_date') else None
            elif query_type is DownloadTypeEnum.track:
                artists = [a.get('name') for a in i.get('artists')]
                year = i.get('publish_date')[:4] if i.get('publish_date') else None

                duration = i.get('length_ms') // 1000
                additional.append(f'{i.get("bpm")}BPM')
            elif query_type is DownloadTypeEnum.album:
                artists = [j.get('name') for j in i.get('artists')]
                year = i.get('publish_date')[:4] if i.get('publish_date') else None
            elif query_type is DownloadTypeEnum.artist:
                artists = None
                year = None
            else:
                raise self.exception(f'Query type "{query_type.name}" is not supported!')

            name = i.get('name')
            name += f' ({i.get("mix_name")})' if i.get("mix_name") else ''

            additional.append(f'Exclusive') if i.get("exclusive") is True else None

            item = SearchResult(
                name=name,
                artists=artists,
                year=year,
                duration=duration,
                result_id=i.get('id'),
                additional=additional if additional != [] else None,
                extra_kwargs={'data': {i.get('id'): i}}
            )

            items.append(item)

        return items

    def get_playlist_info(self, playlist_id: str, is_chart: bool = False) -> PlaylistInfo:
        # get the DJ chart or user playlist
        if is_chart:
            playlist_data = self.session.get_chart(playlist_id)
            playlist_tracks_data = self.session.get_chart_tracks(playlist_id)
        else:
            playlist_data = self.session.get_playlist(playlist_id)
            playlist_tracks_data = self.session.get_playlist_tracks(playlist_id)

        cache = {'data': {}}

        # now fetch all the found total_items
        if is_chart:
            playlist_tracks = playlist_tracks_data.get('results')
        else:
            playlist_tracks = [t.get('track') for t in playlist_tracks_data.get('results')]

        total_tracks = playlist_tracks_data.get('count')
        for page in range(2, (total_tracks - 1) // 100 + 2):
            print(f'Fetching {len(playlist_tracks)}/{total_tracks}', end='\r')
            # get the DJ chart or user playlist
            if is_chart:
                playlist_tracks += self.session.get_chart_tracks(playlist_id, page=page).get('results')
            else:
                # unfold the track element
                playlist_tracks += [t.get('track')
                                    for t in self.session.get_playlist_tracks(playlist_id, page=page).get('results')]

        for i, track in enumerate(playlist_tracks):
            # add the track numbers
            track['track_number'] = i + 1
            track['total_tracks'] = total_tracks
            # add the modified track to the track_extra_kwargs
            cache['data'][track.get('id')] = track

        creator = 'User'
        if is_chart:
            creator = playlist_data.get('person').get('owner_name') if playlist_data.get('person') else 'Beatport'
            release_year = playlist_data.get('change_date')[:4] if playlist_data.get('change_date') else None
            cover_url = playlist_data.get('image').get('dynamic_uri')
        else:
            release_year = playlist_data.get('updated_date')[:4] if playlist_data.get('updated_date') else None
            # always get the first image of the four total images, why is there no dynamic_uri available? Annoying
            cover_url = playlist_data.get('release_images')[0]

        return PlaylistInfo(
            name=playlist_data.get('name'),
            creator=creator,
            release_year=release_year,
            duration=sum([t.get('length_ms') // 1000 for t in playlist_tracks]),
            tracks=[t.get('id') for t in playlist_tracks],
            cover_url=self._generate_artwork_url(cover_url, self.cover_size),
            track_extra_kwargs=cache
        )

    def get_artist_info(self, artist_id: str, get_credited_albums: bool, is_chart: bool = False) -> ArtistInfo:
        artist_data = self.session.get_artist(artist_id)
        artist_tracks_data = self.session.get_artist_tracks(artist_id)

        # now fetch all the found total_items
        artist_tracks = artist_tracks_data.get('results')
        total_tracks = artist_tracks_data.get('count')
        for page in range(2, total_tracks // 100 + 2):
            print(f'Fetching {page * 100}/{total_tracks}', end='\r')
            artist_tracks += self.session.get_artist_tracks(artist_id, page=page).get('results')

        return ArtistInfo(
            name=artist_data.get('name'),
            tracks=[t.get('id') for t in artist_tracks],
            track_extra_kwargs={'data': {t.get('id'): t for t in artist_tracks}},
        )

    def get_album_info(self, album_id: str, data=None, is_chart: bool = False) -> AlbumInfo:
        # check if album is already in album cache, add it
        if data is None:
            data = {}

        album_data = data.get(album_id) if album_id in data else self.session.get_release(album_id)
        tracks_data = self.session.get_release_tracks(album_id)

        # now fetch all the found total_items
        tracks = tracks_data.get('results')
        total_tracks = tracks_data.get('count')
        for page in range(2, total_tracks // 100 + 2):
            print(f'Fetching {len(tracks)}/{total_tracks}', end='\r')
            tracks += self.session.get_release_tracks(album_id, page=page).get('results')

        cache = {'data': {album_id: album_data}}
        for i, track in enumerate(tracks):
            # add the track numbers
            track['number'] = i + 1
            # add the modified track to the track_extra_kwargs
            cache['data'][track.get('id')] = track

        return AlbumInfo(
            name=album_data.get('name'),
            release_year=album_data.get('publish_date')[:4] if album_data.get('publish_date') else None,
            # sum up all the individual track lengths
            duration=sum([t.get('length_ms') // 1000 for t in tracks]),
            upc=album_data.get('upc'),
            cover_url=self._generate_artwork_url(album_data.get('image').get('dynamic_uri'), self.cover_size),
            artist=album_data.get('artists')[0].get('name'),
            artist_id=album_data.get('artists')[0].get('id'),
            tracks=[t.get('id') for t in tracks],
            track_extra_kwargs=cache
        )

    def get_track_info(self, track_id: str, quality_tier: QualityEnum, codec_options: CodecOptions, slug: str = None,
                       data=None, is_chart: bool = False) -> TrackInfo:
        if data is None:
            data = {}

        track_data = data[track_id] if track_id in data else self.session.get_track(track_id)

        album_id = track_data.get('release').get('id')
        album_data = {}
        error = None

        try:
            album_data = data[album_id] if album_id in data else self.session.get_release(album_id)
        except ConnectionError as e:
            # check if the album is region locked
            if 'Territory Restricted.' in str(e):
                error = f"Album {album_id} is region locked"

        track_name = track_data.get('name')
        track_name += f' ({track_data.get("mix_name")})' if track_data.get("mix_name") else ''

        release_year = track_data.get('publish_date')[:4] if track_data.get('publish_date') else None
        genres = [track_data.get('genre').get('name')]
        # check if a second genre exists
        genres += [track_data.get('sub_genre').get('name')] if track_data.get('sub_genre') else []

        tags = Tags(
            album_artist=album_data.get('artists', [{}])[0].get('name'),
            track_number=track_data.get('number'),
            total_tracks=album_data.get('track_count'),
            upc=album_data.get('upc'),
            isrc=track_data.get('isrc'),
            genres=genres,
            release_date=track_data.get('publish_date'),
            copyright=f'© {release_year} {track_data.get("release").get("label").get("name")}',
            extra_tags={
                'BPM': track_data.get('bpm'),
                'Key': track_data.get('key').get('name')
            }
        )

        if not track_data['is_available_for_streaming']:
            error = f'Track "{track_data.get("name")}" is not streamable!'
        elif track_data.get('preorder'):
            error = f'Track "{track_data.get("name")}" is not yet released!'

        track_info = TrackInfo(
            name=track_name,
            album=album_data.get('name'),
            album_id=album_data.get('id'),
            artists=[a.get('name') for a in track_data.get('artists')],
            artist_id=track_data.get('artists')[0].get('id'),
            release_year=release_year,
            duration=track_data.get('length_ms') // 1000,
            bitrate=self.quality_parse[quality_tier],
            bit_depth=None,  # https://en.wikipedia.org/wiki/Audio_bit_depth#cite_ref-1
            cover_url=self._generate_artwork_url(
                track_data.get('release').get('image').get('dynamic_uri'), self.cover_size),
            tags=tags,
            codec=CodecEnum.AAC,
            download_extra_kwargs={'track_id': track_id, 'quality_tier': quality_tier},
            error=error
        )

        return track_info

    def get_track_cover(self, track_id: str, cover_options: CoverOptions, data=None) -> CoverInfo:
        if data is None:
            data = {}

        track_data = data[track_id] if track_id in data else self.session.get_track(track_id)
        cover_url = track_data.get('release').get('image').get('dynamic_uri')

        return CoverInfo(
            url=self._generate_artwork_url(cover_url, cover_options.resolution),
            file_type=ImageFileTypeEnum.jpg)

    def get_track_download(self, track_id: str, quality_tier: QualityEnum) -> TrackDownloadInfo:
        if self.quality_parse[quality_tier] == 128:
            # get the HLS 128k playlist from the API
            stream_data = self.session.get_track_stream(track_id)

            if not stream_data.get('stream_url'):
                raise self.exception('Could not get 128k HLS stream, exiting')

            # HLS
            temp_location = create_temp_filename() + '.mp4'

            if not shutil.which("ffmpeg"):
                raise self.exception('FFmpeg is not installed or working, but FFmpeg is required, exiting')

            ffmpeg.input(stream_data.get('stream_url'), hide_banner=None, y=None).output(temp_location, acodec='copy',
                                                                                         loglevel='error').run()

            # return the MP4 temp file, but tell orpheus to change the container to .m4a (AAC)
            return TrackDownloadInfo(
                download_type=DownloadEnum.TEMP_FILE_PATH,
                temp_file_path=temp_location
            )

        elif self.quality_parse[quality_tier] == 256:
            # get the MP4 256k from the API
            stream_data = self.session.get_track_download(track_id)

            if not stream_data.get('location'):
                raise self.exception('Could not get 256k MP4 stream, exiting')

            # return the MP4 URL
            return TrackDownloadInfo(
                download_type=DownloadEnum.URL,
                file_url=stream_data.get('location')
            )
