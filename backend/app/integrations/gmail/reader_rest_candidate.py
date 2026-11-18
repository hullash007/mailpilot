"""Opt-in fixed-destination REST reader transport. Never selected by an entrypoint.

A new provider is required per browser request. No discovery, background retry,
credential writeback, redirect, proxy environment or ambient credential lookup.
The trusted session factory is a fake-test seam, not browser input.
"""
import json
import time
import requests
from ...services.gmail_reader import MailUnavailable, ReconnectRequired, provider_id
from .identity_adapter import SCOPES, READONLY, TOKEN_URL

BASE = 'https://gmail.googleapis.com/gmail/v1/users/me'


def _secret(value):
    return type(value) is str and 0 < len(value) <= 8192 and all(32 < ord(c) < 127 for c in value)


class RestReadProvider:
    def __init__(self, *, credentials, oauth_config, assert_current,
                 session_factory=requests.Session, clock=time.monotonic):
        # Already-verified grant only; requested scopes are never a fallback.
        if (type(credentials) is not dict or not callable(assert_current)
                or not callable(session_factory) or not callable(clock)
                or credentials.get('client_id') != oauth_config.client_id
                or credentials.get('client_secret') != oauth_config.client_secret
                or credentials.get('token_uri') != TOKEN_URL
                or type(credentials.get('scopes')) not in (list, tuple)
                or READONLY not in credentials['scopes']
                or not set(credentials['scopes']) <= SCOPES
                or not _secret(credentials.get('refresh_token'))):
            raise ReconnectRequired('Reconnect Gmail')
        self._refresh = credentials['refresh_token']
        self._config = oauth_config
        self._assert = assert_current
        self._session = session_factory
        self._clock = clock
        self._token = None
        self._calls = 0

    def _json(self, method, endpoint, *, deadline, limit, params=None, data=None):
        session = response = None
        try:
            self._assert()
            remaining = deadline - self._clock()
            if remaining <= 0 or self._calls >= 32:
                raise MailUnavailable('Read budget exhausted')
            self._calls += 1
            session = self._session()
            session.trust_env = False
            session.max_redirects = 0
            # Fresh requests session has zero automatic retries. No shared pool.
            headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity'}
            if method == 'GET':
                headers['Authorization'] = 'Bearer ' + self._token
            response = session.request(method, endpoint, params=params, data=data,
                headers=headers, timeout=(min(3.05, remaining), min(10, remaining)),
                allow_redirects=False, stream=True, verify=True, proxies={})
            if response.status_code == 401 or (method == 'POST' and response.status_code in (400, 403)):
                raise ReconnectRequired('Reconnect Gmail')
            if response.status_code != 200:
                raise MailUnavailable('Mailbox unavailable')
            length = response.headers.get('content-length')
            if length is not None and (not length.isdecimal() or int(length) > limit):
                raise MailUnavailable('Response too large')
            parts = []; size = 0
            for part in response.iter_content(chunk_size=4096):
                size += len(part)
                if size > limit or self._clock() >= deadline:
                    raise MailUnavailable('Read budget exhausted')
                parts.append(part)
            if self._clock() >= deadline:
                raise MailUnavailable('Read budget exhausted')
            self._assert()  # discard response after disconnect/erase/reconnect
            result = json.loads(b''.join(parts))
            if type(result) is not dict:
                raise MailUnavailable('Invalid response')
            return result
        except ReconnectRequired:
            raise
        except Exception:
            raise MailUnavailable('Mailbox unavailable') from None
        finally:
            if response is not None:
                response.close()
            if session is not None:
                session.close()

    def _get(self, suffix, *, timeout, params=None, limit=4 * 1024 * 1024):
        try:
            if type(timeout) not in (int, float) or not 0 < timeout <= 60:
                raise ValueError()
            deadline = self._clock() + timeout
            if self._token is None:
                value = self._json('POST', TOKEN_URL, deadline=deadline, limit=65536,
                    data={'client_id': self._config.client_id,
                          'client_secret': self._config.client_secret,
                          'refresh_token': self._refresh, 'grant_type': 'refresh_token'})
                if (not _secret(value.get('access_token'))
                        or str(value.get('token_type', '')).casefold() != 'bearer'
                        or type(value.get('expires_in')) is not int or value['expires_in'] <= 0):
                    raise ReconnectRequired('Reconnect Gmail')
                if 'scope' in value:
                    scopes = value['scope']
                    if type(scopes) is not str or READONLY not in scopes.split() or not set(scopes.split()) <= SCOPES:
                        raise ReconnectRequired('Reconnect Gmail')
                if value.get('refresh_token', self._refresh) != self._refresh:
                    # No writer is selected. Never silently discard a rotated grant.
                    raise ReconnectRequired('Reconnect Gmail')
                self._token = value['access_token']
            return self._json('GET', BASE + suffix, deadline=deadline, limit=limit, params=params)
        except ReconnectRequired:
            raise
        except Exception:
            raise MailUnavailable('Mailbox unavailable') from None

    def profile(self, *, timeout):
        return self._get('/profile', timeout=timeout, limit=65536)

    def labels(self, *, timeout):
        return self._get('/labels', timeout=timeout, limit=1024 * 1024)

    def threads(self, *, label, query, page_token, page_size, timeout):
        if type(page_size) is not int or not 1 <= page_size <= 25:
            raise MailUnavailable('Invalid page size')
        params = {'maxResults': page_size, 'includeSpamTrash': 'false'}
        if label: params['labelIds'] = provider_id(label)
        if query: params['q'] = query
        if page_token: params['pageToken'] = page_token
        return self._get('/threads', timeout=timeout, params=params, limit=1024 * 1024)

    def thread(self, tid, *, metadata_only, timeout):
        params = {'format': 'metadata' if metadata_only else 'full'}
        if metadata_only: params['metadataHeaders'] = ['From', 'To', 'Date', 'Subject']
        return self._get('/threads/' + provider_id(tid), timeout=timeout, params=params)
