import json
import re

from utils.utils import create_requests_session


class BeatportApi:
    def __init__(self):
        self.URL = 'https://www.beatport.com/'

        # required for the cookies
        self.s = create_requests_session()

    def headers(self):
        return {
            'user-agent': 'Dalvik/2.1.0 (Linux; U; Android 10; Pixel 3 Build/QP1A.190711.020))',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
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

    def set_session(self, session_cookie: str):
        if 'session' in self.s.cookies:
            del self.s.cookies['session']

        # set a cached session cookie
        self.s.cookies.update({
            'session': session_cookie
        })

    def get_session(self):
        # get the session cookie for temporary_settings_controller
        return self.s.cookies['session']

    def _get(self, endpoint: str, params: dict = None, custom_headers: dict = None, return_json: bool = True):
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

    def get_account_subscription(self):
        # return account subscription with other values
        try:
            account_data = self._get('api/account')
        except ConnectionError:
            return None

        return self._get('api/v4/auth/o/introspect')

    def _get_page(self, endpoint: str, pattern: str, page_slug: str, page_id: str):
        # some stupid template to use for tracks, charts and releases
        html = self._get(f'{endpoint}/{page_slug}/{page_id}', return_json=False, custom_headers={
            'x-pjax': 'true',
            'x-pjax-container': '#pjax-inner-wrapper',
            'x-requested-with': 'XMLHttpRequest'
        })

        page_data = re.search(pattern, html)
        if page_data:
            page_data = page_data.group(0)
            # sometimes there are multiple entries in the javascript, so just remove the ";" if it's the last char
            page_data = page_data[:-1] if page_data[-1] == ';' else page_data
            return json.loads(page_data)
        return None

    def get_track(self, track_slug: str, track_id: str):
        return self._get_page(endpoint='track', pattern='(?<=window.ProductDetail = )[^<]+', page_slug=track_slug,
                              page_id=track_id)

    def get_release(self, release_slug: str, release_id: str):
        return self._get_page(endpoint='release', pattern='(?<=window.ProductDetail = )[^<]+', page_slug=release_slug,
                              page_id=release_id)

    def get_release_tracks(self, release_slug: str, release_id: str):
        release_data = self._get_page(endpoint='release', pattern='(?<=window.Playables = )[^\n]+',
                                      page_slug=release_slug, page_id=release_id)

        if release_data:
            return [track for track in release_data.get('tracks') if track.get('component_type')
                    and 'Tracks' in track.get('component_type')]
        return None

    def get_chart(self, chart_slug: str, chart_id: str):
        # TODO: most likely to break in the future, annoying that chart is the only one without window.ProductDetail
        #  find a fix for the chart metadata without BS4 or XML parsing
        return self._get_page(endpoint='chart', pattern='(?<=    ){[^<]+', page_slug=chart_slug,
                              page_id=chart_id).get('itemListElement')[-1].get('item')

    def get_chart_tracks(self, chart_slug: str, chart_id: str):
        chart_data = self._get_page(endpoint='chart', pattern='(?<=window.Playables = )[^\n]+',
                                    page_slug=chart_slug, page_id=chart_id)

        if chart_data:
            return chart_data.get('tracks')
        return None

    def get_artist(self, artist_slug: str, artist_id: str):
        # TODO: most likely to break in the future, annoying that chart is the only one without window.ProductDetail
        #  find a fix for the chart metadata without BS4 or XML parsing
        return self._get_page(endpoint='artist', pattern='(?<=    )\[[^<]+', page_slug=artist_slug,
                              page_id=artist_id)[-1]

    def get_artist_releases(self, artist_slug: str, artist_id: str):
        artist_data = self._get_page(endpoint='artist', pattern='(?<=window.Playables = )[^\n]+',
                                     page_slug=artist_slug, page_id=artist_id)

        if artist_data:
            return artist_data
        return None

    def get_search_data(self, query: str):
        html = self._get('search', return_json=False, params={'q': query}, custom_headers={
            'x-pjax': 'true',
            'x-pjax-container': '#pjax-inner-wrapper',
            'x-requested-with': 'XMLHttpRequest'
        })

        search_data = re.search('(?<=window.Playables = )[^\n]+', html)
        if search_data:
            search_data = search_data.group(0)
            # sometimes there are multiple entries in the javascript, so just remove the ";" if it's the last char
            search_data = search_data[:-1] if search_data[-1] == ';' else search_data
            return json.loads(search_data)
        return None

    def get_stream(self, track_id: str):
        # get the stream (.m3u8) for a given track id
        return self._get(f'api/v4/catalog/tracks/{track_id}/stream')
