import json
import re
from datetime import timedelta, datetime

from utils.utils import create_requests_session


class BeatportApi:
    def __init__(self):
        self.URL = 'https://www.beatport.com/'
        self.API_URL = 'https://api.beatport.com/v4/'

        self.access_token = None
        self.expires = None

        # required for the cookies
        self.s = create_requests_session()

    def headers(self, use_access_token: bool = False):
        return {
            'user-agent': 'Dalvik/2.1.0 (Linux; U; Android 10; Pixel 3 Build/QP1A.190711.020))',
            'authorization': f'Bearer {self.access_token}' if use_access_token else None,
            'origin': 'https://www.beatport.com',
            'referer': 'https://www.beatport.com/'
        }

    def auth(self, username: str, password: str) -> str:
        # delete old session cookie
        if 'session' in self.s.cookies:
            del self.s.cookies['session']

        # needed for the _csrf_token cookie
        r = self.s.get(f'{self.URL}account/login', headers=self.headers())

        if r.status_code != 200:
            raise ConnectionError(r.text)

        # use the _csrf_token cookie from the get request
        payload = {
            '_csrf_token': self.s.cookies['_csrf_token'],
            'next': '',
            'username': username,
            'password': password
        }

        headers = self.headers()
        # very important to set both x-www-form-urlencoded and referrer!
        headers.update({
            'content-type': 'application/x-www-form-urlencoded',
            'referer': f'{self.URL}account/login'
        })

        r = self.s.post(f'{self.URL}account/login', data=payload, headers=headers)

        # login successful, created a session cookie, so no need to manually save it
        if r.status_code != 200:
            raise ConnectionError(r.text)

        # get the session cookie for temporary_settings_controller
        return self.s.cookies['session']

    def set_session(self, session: dict):
        if 'session' in self.s.cookies:
            del self.s.cookies['session']

        # set a cached session cookie
        self.s.cookies.update({
            'session': session.get('session')
        })

        self.access_token = session.get('access_token')
        self.expires = session.get('expires')

    def get_session(self):
        # get the session cookie for temporary_settings_controller
        return {
            'session': self.s.cookies['session'],
            'access_token': self.access_token,
            'expires': self.expires
        }

    def get_embed_token(self):
        r = self.s.get('https://embed.beatport.com/token', headers=self.headers())

        if r.status_code != 200:
            raise ConnectionError(r)

        r = r.json()
        self.access_token = r.get('access_token')
        self.expires = datetime.now() + timedelta(seconds=r.get('expires_in'))

        return self.access_token

    # TODO: remove the old get method in favor of the new _get_api() one
    def _get(self, endpoint: str, params: dict = None, custom_headers: dict = None,
             return_json: bool = True):
        # function for ALL get requests
        headers = custom_headers if custom_headers else self.headers()

        if not params:
            params = {}

        r = self.s.get(f'{self.URL}{endpoint}', params=params, headers=headers)

        if r.status_code not in {200, 201, 202}:
            raise ConnectionError(r.text)

        # check if json is wanted, by default else decode the content with UTF-8
        if return_json:
            return r.json()
        return r.content.decode('UTF-8')

    def _get_api(self, endpoint: str, params: dict = None):
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

    def get_account_subscription(self):
        # return account subscription with other values
        try:
            account_data = self._get('api/account')
        except ConnectionError:
            return None

        return self._get('api/v4/auth/o/introspect')

    def get_track(self, track_id: str):
        return self._get_api(f'catalog/tracks/{track_id}')

    def get_release(self, release_id: str):
        return self._get_api(f'catalog/releases/{release_id}')

    def get_release_tracks(self, release_id: str, page: int = 1, per_page: int = 100):
        return self._get_api(f'catalog/releases/{release_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_chart(self, chart_id: str):
        return self._get_api(f'catalog/charts/{chart_id}')

    def get_chart_tracks(self, chart_id: str, page: int = 1, per_page: int = 100):
        return self._get_api(f'catalog/charts/{chart_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_artist(self, artist_id: str):
        return self._get_api(f'catalog/artists/{artist_id}')

    def get_artist_tracks(self, artist_id: str, page: int = 1, per_page: int = 100):
        return self._get_api(f'catalog/artists/{artist_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_label(self, label_id: str):
        return self._get_api(f'catalog/labels/{label_id}')

    def get_label_releases(self, label_id: str):
        return self._get_api(f'catalog/labels/{label_id}/releases')

    def get_search(self, query: str):
        return self._get_api('catalog/search', params={'q': query})

    def get_stream(self, track_id: str):
        # get the stream (.m3u8) for a given track id
        return self._get(f'api/v4/catalog/tracks/{track_id}/stream')

    def valid_token(self):
        try:
            self._get_api('catalog/tracks/10844269')
            return True
        except ValueError:
            return False


if __name__ == '__main__':
    api = BeatportApi()

