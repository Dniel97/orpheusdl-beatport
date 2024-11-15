from datetime import timedelta, datetime

from utils.utils import create_requests_session


class BeatportApi:
    def __init__(self):
        self.API_URL = 'https://api.beatport.com/v4/'

        # client id from the Beatport android app
        self.client_id = 'nBQh4XCUqE0cpoy609mC8GoyjCcJHBwbI374FYmE'

        self.access_token = None
        self.refresh_token = None
        self.expires = None

        # required for the cookies
        self.s = create_requests_session()

    def headers(self, use_access_token: bool = False):
        return {
            #'user-agent': 'libbeatport/v2.4.1-8-g1e7ba687a',
            'authorization': f'Bearer {self.access_token}' if use_access_token else None,
            # 'X-LINK-DEVICE-ID': str(uuid.uuid4()),
        }

    def auth(self, username: str, password: str) -> dict:
        r = self.s.post(f'{self.API_URL}auth/o/token/', data={
            'client_id': self.client_id,
            #'client_secret': '7oBWZwYOia9u4yblRmVTTet5sficrN7xbbCglbmRxoN08ShlpxyXbixLeov2wC62R3WsD2dxSTwLosi71FqpfLS'
            #                 'OKnFSZ4FTXoayHNLHpWz7XcmyOMiLkqnbTPk2kI9L',
            'username': username,
            'password': password,
            'grant_type': 'password',
        })

        if r.status_code != 200:
            return r.json()

        # convert to JSON
        r = r.json()

        # save all tokens with access_token expiry date
        self.access_token = r['access_token']
        self.refresh_token = r['refresh_token']
        self.expires = datetime.now() + timedelta(seconds=r['expires_in'])

        return r

    def refresh(self):
        r = self.s.post(f'{self.API_URL}auth/o/token/', data={
            'client_id': self.client_id,
            'refresh_token': self.refresh_token,
            'grant_type': 'refresh_token',
        })

        if r.status_code != 200:
            return r.json()

        self.access_token = r.json()['access_token']
        self.refresh_token = r.json()['refresh_token']
        self.expires = datetime.now() + timedelta(seconds=r.json()['expires_in'])

    def set_session(self, session: dict):
        self.access_token = session.get('access_token')
        self.refresh_token = session.get('refresh_token')
        self.expires = session.get('expires')

    def get_session(self):
        return {
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
            'expires': self.expires
        }

    def _get(self, endpoint: str, params: dict = None):
        # function for API requests
        if not params:
            params = {}

        r = self.s.get(f'{self.API_URL}{endpoint}', params=params, headers=self.headers(use_access_token=True))

        # access_token expired
        if r.status_code == 401:
            raise ValueError(r.text)

        if r.status_code not in {200, 201, 202}:
            raise ConnectionError(r.text)

        return r.json()

    def get_account(self):
        return self._get('auth/o/introspect')

    def get_track(self, track_id: str):
        return self._get(f'catalog/tracks/{track_id}')

    def get_release(self, release_id: str):
        return self._get(f'catalog/releases/{release_id}')

    def get_release_tracks(self, release_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/releases/{release_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_playlist(self, playlist_id: str):
        return self._get(f'catalog/playlists/{playlist_id}')

    def get_playlist_tracks(self, playlist_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/playlists/{playlist_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_chart(self, chart_id: str):
        return self._get(f'catalog/charts/{chart_id}')

    def get_chart_tracks(self, chart_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/charts/{chart_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_artist(self, artist_id: str):
        return self._get(f'catalog/artists/{artist_id}')

    def get_artist_tracks(self, artist_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/artists/{artist_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_label(self, label_id: str):
        return self._get(f'catalog/labels/{label_id}')

    def get_label_releases(self, label_id: str):
        return self._get(f'catalog/labels/{label_id}/releases')

    def get_search(self, query: str):
        return self._get('catalog/search', params={'q': query})

    def get_track_stream(self, track_id: str):
        # get the 128k stream (.m3u8) for a given track id from needledrop.beatport.com
        return self._get(f'catalog/tracks/{track_id}/stream')

    def get_track_download(self, track_id: str, quality: str):
        # get the 256k stream (.mp4) for a given track id
        return self._get(f'catalog/tracks/{track_id}/download', params={'quality': quality})
