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
    session_storage_variables=['session', 'access_token', 'expires'],
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
            QualityEnum.MINIMUM: '128k',
            QualityEnum.LOW: '128k',
            QualityEnum.MEDIUM: '128k',
            QualityEnum.HIGH: '256k',
            QualityEnum.LOSSLESS: '256k',
            QualityEnum.HIFI: '256k'
        }

        self.session = BeatportApi()
        session = {
            'session': module_controller.temporary_settings_controller.read('session'),
            'access_token': module_controller.temporary_settings_controller.read('access_token')
        }

        if session.get('session'):
            logging.debug(f'Beatport: session found, loading')

            self.session.set_session(session)

            if not self.session.valid_token():
                logging.debug(f'Beatport: access_token expired, getting a new one')
                session['access_token'] = self.session.get_embed_token()

                # saving new access_token in temporary and in the api session
                self.session.set_session(session)
                self.module_controller.temporary_settings_controller.set('access_token', session.get('access_token'))

            # make sure to get a new cookie if the old expired
            if not self.valid_account():
                self.login(module_controller.module_settings.get('username'),
                           module_controller.module_settings.get('password'))

    def login(self, email: str, password: str):
        logging.debug(f'Beatport: no session found, login')
        session_cookie = self.session.auth(email, password)
        access_token = self.session.get_embed_token()
        if not self.valid_account():
            # TODO: more precise error message
            raise self.exception('Username/Password is wrong or no active subscription!')

        self.module_controller.temporary_settings_controller.set('session', session_cookie)
        self.module_controller.temporary_settings_controller.set('access_token', access_token)

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
            media_id=media_id
        )

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
            if query_type is DownloadTypeEnum.playlist:
                artists = [i.get('person').get('owner_name') if i.get('person') else 'Beatport']
                year = i.get('date').get('released')[:4] if i.get('date') else None
            elif query_type is DownloadTypeEnum.track:
                artists = [a.get('name') for a in i.get('artists')]
                year = i.get('date').get('released')[:4] if i.get('date') else None

                additional.append(f'{i.get("bpm")}BPM - {i.get("key")}')
            elif query_type is DownloadTypeEnum.album:
                artists = [j.get('name') for j in i.get('artists')]
                year = i.get('date').get('released')[:4] if i.get('date') else None
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
                result_id=i.get('id'),
                additional=additional if additional != [] else None,
                extra_kwargs={'data': {i.get('id'): i}}
            )

            items.append(item)

        return items

    def get_playlist_info(self, playlist_id: str) -> PlaylistInfo:
        playlist_data = self.session.get_chart(playlist_id)
        playlist_tracks = self.session.get_chart_tracks(playlist_id)

        cache = {'data': {}}
        total_tracks = len(playlist_tracks.get('results'))

        for i, track in enumerate(playlist_tracks.get('results')):
            # add the track numbers
            track['track_number'] = i + 1
            track['total_tracks'] = total_tracks
            # add the modified track to the track_extra_kwargs
            cache['data'][track.get('id')] = track

        return PlaylistInfo(
            name=playlist_data.get('name'),
            creator=playlist_data.get('person').get('owner_name') if playlist_data.get('person') else 'Beatport',
            release_year=playlist_data.get('change_date')[:4] if playlist_data.get('change_date') else None,
            tracks=[t.get('id') for t in playlist_tracks.get('results')],
            cover_url=playlist_data.get('image').get('uri'),
            track_extra_kwargs=cache
        )

    def get_artist_info(self, artist_id: str, get_credited_albums: bool) -> ArtistInfo:
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

    def get_album_info(self, album_id: str, data=None) -> AlbumInfo:
        # check if album is already in album cache, add it
        if data is None:
            data = {}

        album_data = data.get(album_id) if album_id in data else self.session.get_release(album_id)
        tracks_data = self.session.get_release_tracks(album_id)

        # now fetch all the found total_items
        tracks = tracks_data.get('results')
        total_tracks = tracks_data.get('count')
        for page in range(2, total_tracks // 100 + 2):
            print(f'Fetching {page * 100}/{total_tracks}', end='\r')
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
            upc=album_data.get('upc'),
            cover_url=album_data.get('image').get('url'),
            artist=album_data.get('artists')[0].get('name'),
            artist_id=album_data.get('artists')[0].get('id'),
            tracks=[t.get('id') for t in tracks],
            track_extra_kwargs=cache
        )

    def get_track_info(self, track_id: str, quality_tier: QualityEnum, codec_options: CodecOptions, slug: str = None,
                       data=None) -> TrackInfo:
        if data is None:
            data = {}

        track_data = data[track_id] if track_id in data else self.session.get_track(track_id)

        album_id = track_data.get('release').get('id')
        album_data = data[album_id] if album_id in data else self.session.get_release(album_id)

        track_name = track_data.get('name')
        track_name += f' ({track_data.get("mix_name")})' if track_data.get("mix_name") else ''

        release_year = track_data.get('publish_date')[:4] if track_data.get('publish_date') else None
        genres = [track_data.get('genre').get('name')]
        # check if a second genre exists
        genres += [track_data.get('sub_genre').get('name')] if track_data.get('sub_genres') else []

        tags = Tags(
            album_artist=album_data.get('artists')[0].get('name'),
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

        # get the HLS playlist from the API
        stream_data = self.session.get_stream(track_id)
        stream_url = None
        # check if a playlist got returned
        if stream_data.get('stream_url'):
            # now get the wanted quality
            stream_url = stream_data.get('stream_url').replace('128k', self.quality_parse[quality_tier])

        error = None
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
            bitrate=int(self.quality_parse[quality_tier][:3]),
            cover_url=track_data.get('release').get('image').get('uri'),
            tags=tags,
            codec=CodecEnum.AAC,
            download_extra_kwargs={'stream_url': stream_url},
            error=error
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
