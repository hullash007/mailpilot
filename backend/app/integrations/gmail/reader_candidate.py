"""Opt-in read-only adapter over an explicitly supplied authorized Gmail service.

No build(), OAuth, token refresh, environment, scheduler or persistence here.
The service and timeout setter are trusted dependencies. Actual HTTP size,
redirect, refresh-token custody and credential writeback need separate release
qualification; a fake service is not proof of that transport.
"""
from ...services.gmail_reader import ReconnectRequired

class GmailReadProvider:
    def __init__(self,*,service,set_timeout,granted_scopes):
        allowed={'openid','email','https://www.googleapis.com/auth/userinfo.email','https://www.googleapis.com/auth/gmail.readonly'}
        if not callable(set_timeout) or not isinstance(granted_scopes,(tuple,list)) or 'https://www.googleapis.com/auth/gmail.readonly' not in granted_scopes or not set(granted_scopes)<=allowed:
            raise ValueError('Explicit readonly service and granted scopes required')
        self.service,self.set_timeout=service,set_timeout
    def run(self,request,timeout):
        if not 0<timeout<=60:raise ValueError('Bounded request timeout required')
        self.set_timeout(timeout)
        try:return request.execute(num_retries=0)
        except Exception as e:
            if getattr(getattr(e,'resp',None),'status',None)==401:raise ReconnectRequired('Reconnect Gmail') from None
            raise
    def profile(self,*,timeout):return self.run(self.service.users().getProfile(userId='me'),timeout)
    def labels(self,*,timeout):return self.run(self.service.users().labels().list(userId='me'),timeout)
    def threads(self,*,label,query,page_token,page_size,timeout):
        args=dict(userId='me',maxResults=page_size,pageToken=page_token,q=query or None,includeSpamTrash=False)
        if label:args['labelIds']=[label]
        return self.run(self.service.users().threads().list(**args),timeout)
    def thread(self,tid,*,metadata_only,timeout):
        args=dict(userId='me',id=tid,format='metadata' if metadata_only else 'full')
        if metadata_only:args['metadataHeaders']=['From','To','Date','Subject']
        return self.run(self.service.users().threads().get(**args),timeout)
