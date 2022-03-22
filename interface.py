import logging
import shutil
import ffmpeg

from urllib.parse import urlparse

from utils.models import *
from utils.utils import create_temp_filename
from .beatport_api import BeatportApi

module_information = ModuleInformation(
    service_name='Beatport',
    module_supported_modes=ModuleModes.download | ModuleModes.covers,
    session_settings={'username': '', 'password': ''},
    session_storage_variables=['session'],
    netlocation_constant='beatport',
    url_decoding=ManualEnum.manual,
    test_url='https://www.beatport.com/track/darkside/10844269'
)


class ModuleInterface:
    # noinspection PyTypeChecker
    def __init__(self, module_controller: ModuleController):
        self.exception = module_controller.module_error
        self.oprinter = module_controller.printer_controller
        self.print = module_controller.printer_controller.oprint
        self.module_controller = module_controller

        # LOW = 128kbit/s AAC, MEDIUM = 128kbit/s AAC, HIGH = 256kbit/s AAC,
        self.quality_parse = {
            QualityEnum.LOW: '128k',
            QualityEnum.MEDIUM: '128k',
            QualityEnum.HIGH: '256k',
            QualityEnum.LOSSLESS: '256k',
            QualityEnum.HIFI: '256k'
        }

        # TODO: Remove the album_cache! Currently required, because get_album_info() needs a slug which cannot
        #  be provided everytime
        self.album_cache = {}

        self.session = BeatportApi()
        session_cookie = module_controller.temporary_settings_controller.read('session')
        if session_cookie:
            logging.debug(f'Beatport: session found, loading')
            self.session.set_session(session_cookie)

            # make sure to get a new cookie if the old expired
            if not self.valid_account():
                self.login(module_controller.module_settings.get('username'), module_controller.module_settings.get('password'))

    def login(self, email: str, password: str):
        logging.debug(f'Beatport: no session found, login')
        session_cookie = self.session.auth(email, password)
        if not self.valid_account():
            # TODO: more precise error message
            raise self.exception('Username/Password is wrong or no active subscription!')

        self.module_controller.temporary_settings_controller.set('session', session_cookie)

    def valid_account(self):
        # get the subscription from the API and check if it's at least a "Link" subscription
        account_data = self.session.get_account_subscription()
        if account_data and 'link' in account_data.get('subscription'):
            return True
        return False

    @staticmethod
    def custom_url_parse(link: str):
        url = urlparse(link)
        components = url.path.split('/')

        if not components or len(components) <= 2:
            print(f'\tInvalid URL: {link}')
            return

        if len(components) == 3 or len(components) == 4:
            type_ = components[1]
            media_slug = components[2]
            media_id = components[3]
        else:
            print(f'\tInvalid URL: {link}')
            return

        if type_ == 'track':
            media_type = DownloadTypeEnum.track
        elif type_ == 'release':
            media_type = DownloadTypeEnum.album
        elif type_ == 'chart':
            media_type = DownloadTypeEnum.playlist
        elif type_ == 'artist':
            media_type = DownloadTypeEnum.artist
        else:
            print(f'\t{type_} not supported!')
            return

        return MediaIdentification(
            media_type=media_type,
            media_id=media_id,
            extra_kwargs={'slug': media_slug}
        )

    def search(self, query_type: DownloadTypeEnum, query: str, track_info: TrackInfo = None, limit: int = 20):
        results = self.session.get_search_data(query)

        # TODO: support artist search, maybe BS4?
        name_parse = {
            'track': 'tracks',
            'album': 'releases',
            'playlist': 'charts',
            'artist': 'tracks'
        }

        items = []
        for i in results.get(name_parse.get(query_type.name)):
            additional = []
            if query_type is DownloadTypeEnum.playlist:
                artists = [i.get('dj_profile_name') if i.get('dj_profile_name') else 'Beatport']
                year = i.get('date').get('released')[:4] if i.get('date') else None
            elif query_type is DownloadTypeEnum.track:
                artists = [a.get('name') for a in i.get('artists')]
                year = i.get('date').get('released')[:4] if i.get('date') else None

                additional.append(f'{i.get("bpm")}BPM - {i.get("key")}')
            elif query_type is DownloadTypeEnum.album:
                artists = [j.get('name') for j in i.get('artists')]
                year = i.get('date').get('released')[:4] if i.get('date') else None
            else:
                raise self.exception(f'Query type "{query_type.name}" is not supported!')

            name = i.get('name')
            name += f' ({i.get("mix")})' if i.get("mix") else ''

            additional.append(f'Exclusive') if i.get("exclusive") is True else None

            item = SearchResult(
                name=name,
                artists=artists,
                year=year,
                result_id=i.get('id'),
                additional=additional if additional != [] else None,
                extra_kwargs={'data': {i.get('id'): i}}
            )

            items.append(item)

        return items

    def get_playlist_info(self, playlist_id: str, slug: str) -> PlaylistInfo:
        # TODO: get more metadata from session.get_chart()
        playlist_data = self.session.get_chart(slug, playlist_id)
        playlist_tracks = self.session.get_chart_tracks(slug, playlist_id)

        cache = {'data': {}}
        total_tracks = len(playlist_tracks)

        for i, track in enumerate(playlist_tracks):
            # add the track numbers
            track['track_number'] = i + 1
            track['total_tracks'] = total_tracks
            # add the modified track to the track_extra_kwargs
            cache['data'][track.get('id')] = track

        return PlaylistInfo(
            name=playlist_data.get('title'),
            creator='Beatport',
            # TODO: don't assume
            release_year=2022,
            tracks=[t.get('id') for t in playlist_tracks],
            cover_url=playlist_data.get('image'),
            track_extra_kwargs=cache
        )

    def get_artist_info(self, artist_id: str, get_credited_albums: bool, slug: str) -> ArtistInfo:
        # TODO: get all albums not just the 20 first, use
        #  https://www.beatport.com/api/slidables/latest-releases-for-artist/469376?page=3&per_page=10
        artist_data = self.session.get_artist(slug, artist_id)
        artist_releases = self.session.get_artist_releases(slug, artist_id)

        return ArtistInfo(
            name=artist_data.get('@name'),
            albums=[a.get('id') for a in artist_releases.get('releases')],
            album_extra_kwargs={'data': {a.get('id'): a for a in artist_releases.get('releases')}},
            tracks=[t.get('id') for t in artist_releases.get('tracks')],
            track_extra_kwargs={'data': {t.get('id'): t for t in artist_releases.get('tracks')}},
        )

    def get_album_info(self, album_id: str, slug: str = None, data=None) -> AlbumInfo:
        # check if album is already in album cache, add it
        if data is None:
            data = {}

        if album_id in self.album_cache:
            album_data = self.album_cache[album_id]
            slug = album_data.get('slug')
        elif album_id in data:
            album_data = data[album_id]
            slug = album_data.get('slug')
        else:
            album_data = self.session.get_release(slug, album_id)

        cache = {'data': {}}
        tracks_data = []
        if slug:
            tracks_data = self.session.get_release_tracks(slug, album_id)

            total_tracks = len(tracks_data)
            for i, track in enumerate(tracks_data):
                if 'Tracks' in track.get('component_type'):
                    # add the track numbers
                    track['track_number'] = i + 1
                    track['total_tracks'] = total_tracks
                    # add the modified track to the track_extra_kwargs
                    cache['data'][track.get('id')] = track

        return AlbumInfo(
            name=album_data.get('name'),
            release_year=album_data.get('date').get('released')[:4] if album_data.get('date') else None,
            upc=album_data.get('catalog'),
            cover_url=album_data.get('images').get('large').get('url'),
            artist=album_data.get('artists')[0].get('name'),
            artist_id=album_data.get('artists')[0].get('id'),
            tracks=[t.get('id') for t in tracks_data],
            track_extra_kwargs=cache
        )

    def get_track_info(self, track_id: str, quality_tier: QualityEnum, codec_options: CodecOptions, slug: str = None,
                       data=None) -> TrackInfo:
        if data is None:
            data = {}

        track_data = data[track_id] if track_id in data else self.session.get_track(slug, track_id)
        album_data = self.session.get_release(track_data.get('release').get('slug'),
                                              track_data.get('release').get('id'))
        # TODO: Remove it, but it's still required
        self.album_cache[track_data.get('release').get('id')] = album_data

        track_name = track_data.get('name')
        track_name += f' ({track_data.get("mix")})' if track_data.get("mix") else ''

        release_year = track_data.get('date').get('released')[:4] if track_data.get('date') else None

        # TODO: get the correct track_number from get_album_info()
        tags = Tags(
            album_artist=album_data.get('artists')[0].get('name'),
            track_number=track_data.get('track_number') or 1,
            total_tracks=track_data.get('total_tracks') or 1,
            upc=album_data.get('catalog'),
            genres=[g.get('name') for g in track_data.get('genres')] + [g.get('name') for g in
                                                                        track_data.get('sub_genres')],
            release_date=album_data.get('date').get('released') if album_data.get('date') else None,
            copyright=f'© {release_year} {track_data.get("label").get("name")}'
        )

        # get the HLS playlist from the API
        stream_data = self.session.get_stream(track_id)
        stream_url = None
        # check if a playlist got returned
        if stream_data.get('stream_url'):
            # now get the wanted quality
            stream_url = stream_data.get('stream_url').replace('128k', self.quality_parse[quality_tier])

        track_info = TrackInfo(
            name=track_name,
            album=album_data.get('name'),
            album_id=album_data.get('id'),
            artists=[a.get('name') for a in track_data.get('artists')],
            artist_id=track_data.get('artists')[0].get('id'),
            release_year=track_data.get('date').get('released')[:4] if track_data.get('date') else None,
            bitrate=int(self.quality_parse[quality_tier][:3]),
            cover_url=track_data.get('images').get('large').get('url'),
            explicit=track_data.get('explicit'),
            tags=tags,
            codec=CodecEnum.AAC,
            download_extra_kwargs={'stream_url': stream_url},
            error=f'Track "{track_data.get("name")}" is not streamable!' if not track_data[
                'is_available_for_streaming'] else None
        )

        return track_info

    def get_track_download(self, stream_url: str = None) -> TrackDownloadInfo:
        # HLS
        temp_location = create_temp_filename() + '.mp4'

        if not shutil.which("ffmpeg"):
            raise self.exception('FFmpeg is not installed or working, but FFmpeg is required, exiting')

        ffmpeg.input(stream_url, hide_banner=None, y=None).output(temp_location, acodec='copy', loglevel='error').run()

        # return the MP4 temp file, but tell orpheus to change the container to .m4a (AAC)
        return TrackDownloadInfo(
            download_type=DownloadEnum.TEMP_FILE_PATH,
            temp_file_path=temp_location
        )
