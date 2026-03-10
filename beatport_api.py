from datetime import timedelta, datetime

from utils.utils import create_requests_session


class BeatportError(Exception):
    def __init__(self, message):
        self.message = message
        super(BeatportError, self).__init__(message)


class BeatportApi:
    def __init__(self):
        self.API_URL = "https://api.beatport.com/v4/"

        # client id from Serato DJ Lite
        self.client_id = "Zy2K9Wvy6DkUds7g8s1GNMHfk17E5Ch2BWHlyaGY"
        self.redirect_uri = "seratodjlite://beatport"

        self.access_token = None
        self.refresh_token = None
        self.expires = None

        # required for the cookies
        self.s = create_requests_session()

    def get_anonymous_token(self):
        import re
        import json
        r = self.s.get("https://www.beatport.com/", headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        })
        if r.status_code != 200:
            raise ConnectionError(f"Failed to get Beatport homepage ({r.status_code})")

        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, flags=re.DOTALL)
        if not match:
            raise BeatportError("Could not find __NEXT_DATA__ on Beatport homepage")

        data = json.loads(match.group(1))

        def find_anon_session(obj):
            if isinstance(obj, dict):
                # Target the specific 'anonSession' object if possible
                if 'anonSession' in obj and isinstance(obj['anonSession'], dict):
                    if 'access_token' in obj['anonSession']:
                        return obj['anonSession']
                
                # Check for access_token in the current level as well
                if 'access_token' in obj:
                    return obj
                    
                for k, v in obj.items():
                    res = find_anon_session(v)
                    if res: return res
            elif isinstance(obj, list):
                for item in obj:
                    res = find_anon_session(item)
                    if res: return res
            return None

        token_data = find_anon_session(data)
        if not token_data or 'access_token' not in token_data:
            raise BeatportError("Could not find anonymous access token on Beatport homepage")

        self.access_token = token_data['access_token']
        self.refresh_token = None
        expires_in = token_data.get('expires_in', 3600)
        self.expires = datetime.now() + timedelta(seconds=expires_in)

    def headers(self, use_access_token: bool = False):
        return {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
            'authorization': f'Bearer {self.access_token}' if use_access_token else None,
            'referer': 'https://www.beatport.com/',
            'origin': 'https://www.beatport.com'
        }

    def auth(self, username: str, password: str) -> dict:
        acc_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/131.0.0.0 Safari/537.36",
        }

        # authorize the code_challenge
        r = self.s.get(f"{self.API_URL}auth/o/authorize/", params={
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
        }, headers=acc_headers, allow_redirects=False)

        if r.status_code != 302:
            raise ConnectionError(r.text)

        # get the referer url from the last request
        base_url = r.request.url.replace(r.request.path_url, '')
        referer = base_url + r.headers['location']

        r = self.s.post(f"{self.API_URL}auth/login/", json={
            "username": username,
            "password": password,
        }, headers={**acc_headers, "Referer": referer})

        if r.status_code != 200:
            # Check for blank field errors and provide a better message
            try:
                error_data = r.json()
                if isinstance(error_data, dict):
                    if "username" in error_data and "password" in error_data:
                        username_errors = error_data.get("username", [])
                        password_errors = error_data.get("password", [])
                        if any("blank" in str(msg).lower() for msg in username_errors) and \
                           any("blank" in str(msg).lower() for msg in password_errors):
                            raise BeatportError(
                                "Beatport credentials are missing in settings.json. "
                                "Please fill in: username, password. "
                                "Use the OrpheusDL GUI Settings tab (Beatport) or edit config/settings.json directly."
                            )
            except (ValueError, KeyError):
                pass  # If JSON parsing fails, fall through to original error
            raise ConnectionError(r.text)

        # get the code from the redirect url, that's why redirect is disabled
        r = self.s.get(f"{self.API_URL}auth/o/authorize/", params={
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
        }, headers=acc_headers, allow_redirects=False)

        if r.status_code != 302:
            raise ConnectionError(r.text)

        # get the code from the redirect url
        code = r.headers['location'].split('code=')[1]

        # exchange the code for the access_token, refresh_token and expires_in
        r = self.s.post(f"{self.API_URL}auth/o/token/", data={
            "client_id": self.client_id,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        })

        if r.status_code != 200:
            raise ConnectionError(r.text)

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

        # access_token expired or invalid
        if r.status_code == 401:
            if not self.refresh_token:
                # Forced refresh for anonymous sessions
                self.get_anonymous_token()
            else:
                err = self.refresh()
                if err:
                    raise ValueError(f"Refresh failed: {r.text}")
            
            # retry request
            r = self.s.get(f'{self.API_URL}{endpoint}', params=params, headers=self.headers(use_access_token=True))

            if r.status_code == 401:
                import logging
                logging.error(f"Beatport API Authentication failed after retry for endpoint: {endpoint}")
                logging.error(f"  Response: {r.text[:500]}")
                raise ValueError(f"Authentication failed after retry: {r.text}")

        # check if territory is not allowed or other access issues
        if r.status_code == 403:
            try:
                response_data = r.json()
                detail = response_data.get("detail", "")
                error_code = response_data.get("error_code", "")
                error_message = response_data.get("error", "")
                message = response_data.get("message", "")
                
                import logging
                logging.warning(f"Beatport API 403 error - endpoint: {endpoint}")
                logging.warning(f"  detail: {detail}")
                logging.warning(f"  error_code: {error_code}")
                logging.warning(f"  error: {error_message}")
                logging.warning(f"  message: {message}")
                logging.warning(f"  full_response: {response_data}")
                
                # Collect all error text fields
                all_error_text = " ".join(filter(None, [detail, error_message, message]))
                all_error_text_lower = all_error_text.lower()
                
                # EXTREMELY conservative territory checking - only raise region locked if VERY EXPLICITLY about territory/region restrictions
                # The API might return 403 for many reasons (subscription, account issues, etc.) - don't assume region lock
                # Since tracks can be available on the website but API returns 403, we should be VERY careful
                explicit_region_phrases = [
                    "not available in your territory",
                    "not available in your region", 
                    "not available in this territory",
                    "not available in this region",
                    "territory not allowed",
                    "region not allowed",
                    "geographic restrictions apply",
                    "territorial restrictions apply",
                    "this content is not available in your territory",
                    "this content is not available in your region"
                ]
                
                # Check if any explicit region phrase appears - must be very explicit and complete
                # Don't match partial phrases like just "territory" or "region"
                is_region_locked = False
                if all_error_text_lower:
                    import re
                    for phrase in explicit_region_phrases:
                        # Check if phrase appears as a complete phrase with word boundaries
                        pattern = r'\b' + re.escape(phrase) + r'\b'
                        if re.search(pattern, all_error_text_lower):
                            is_region_locked = True
                            logging.warning(f"  Detected explicit region lock phrase: {phrase}")
                            break
                    
                    # Also check for "territory restricted" or "region restricted" but only if they appear as complete phrases
                    if not is_region_locked:
                        if re.search(r'\bterritory\s+restricted\b', all_error_text_lower) or \
                           re.search(r'\bregion\s+restricted\b', all_error_text_lower):
                            is_region_locked = True
                            logging.warning(f"  Detected explicit region lock: territory/region restricted")
                
                if is_region_locked:
                    # Log which phrase was detected
                    import sys
                    print(f"[DEBUG] Raising 'region locked' based on detected phrase", file=sys.stderr)
                    raise BeatportError("region locked")
                elif "subscription" in all_error_text_lower:
                    raise BeatportError("subscription required")
                elif "not available" in all_error_text_lower and ("download" in all_error_text_lower or "stream" in all_error_text_lower):
                    raise BeatportError("content not available")
                else:
                    # Log that we're NOT treating this as region locked
                    import sys
                    print(f"[DEBUG] NOT treating as region locked - no explicit region phrases found", file=sys.stderr)
                    print(f"[DEBUG] All error text: {all_error_text}", file=sys.stderr)
                    # For other 403 errors, show the actual error message from API
                    # This is likely NOT a region lock - could be subscription, account issue, or API problem
                    error_msg = detail if detail else (error_message if error_message else (message if message else "access denied (HTTP 403)"))
                    # Don't label it as region locked - show the actual API error
                    raise BeatportError(f"API error: {error_msg}")
            except BeatportError:
                # Re-raise BeatportError as-is
                raise
            except (ValueError, KeyError) as e:
                # If we can't parse JSON, log the raw response and give a generic 403 error
                import logging
                logging.warning(f"Beatport API 403 error - could not parse JSON response: {r.text[:500]}")
                raise BeatportError(f"API error (HTTP 403): {r.text[:200] if r.text else 'Unable to parse error response'}")
        
        # Check for 404 errors (not found)
        if r.status_code == 404:
            try:
                response_data = r.json()
                detail = response_data.get("detail", "")
                error_message = response_data.get("error", "")
                message = response_data.get("message", "")
                
                # Log the full response for debugging
                import logging
                logging.warning(f"Beatport API 404 error - endpoint: {endpoint}")
                logging.warning(f"  detail: {detail}")
                logging.warning(f"  error: {error_message}")
                logging.warning(f"  message: {message}")
                logging.warning(f"  full_response: {response_data}")
                
                # Return a clear "not found" error
                error_msg = detail if detail else (error_message if error_message else (message if message else "not found"))
                raise BeatportError(f"not found: {error_msg}")
            except BeatportError:
                raise
            except (ValueError, KeyError):
                # If we can't parse JSON, just give a generic 404 error
                import logging
                logging.warning(f"Beatport API 404 error - could not parse JSON response: {r.text[:500]}")
                raise BeatportError(f"not found (HTTP 404)")

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

    def get_artist_releases(self, artist_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/artists/{artist_id}/releases', params={
            'page': page,
            'per_page': per_page
        })

    def get_label(self, label_id: str):
        return self._get(f'catalog/labels/{label_id}')

    def get_label_releases(self, label_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/labels/{label_id}/releases', params={
            'page': page,
            'per_page': per_page
        })

    def get_label_tracks(self, label_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/labels/{label_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_search(self, query: str, search_type: str = None, page: int = 1, per_page: int = 100):        
        params = {'q': query}
        if search_type:
            params['type'] = search_type
            # only add pagination params if a type filter is active
            params['page'] = page
            params['per_page'] = per_page
        # else (no search_type), the API returns a multi-category summary without pagination
        return self._get('catalog/search', params=params)

    def get_track_stream(self, track_id: str):
        # get the 128k stream (.m3u8) for a given track id from needledrop.beatport.com
        return self._get(f'catalog/tracks/{track_id}/stream')

    def get_track_download(self, track_id: str, quality: str):
        # get the 256k stream (.mp4) for a given track id
        return self._get(f'catalog/tracks/{track_id}/download', params={'quality': quality})