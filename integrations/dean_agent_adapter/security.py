"""Small fixed-algorithm delegation envelope; replay state belongs to PostgreSQL."""
import base64
import hashlib
import hmac
import json
import time
from uuid import UUID, uuid4


def _encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _decode(text):
    return base64.b64decode(text + '=' * (-len(text) % 4), altchars=b'-_', validate=True)


def sign(secret, claims, audience, lifetime=60):
    if len(secret) < 32:
        raise ValueError('Delegation secret needs at least 32 characters')
    body = {**claims, 'aud': audience, 'exp': int(time.time()) + lifetime, 'nonce': str(uuid4())}
    encoded = _encode(json.dumps(body, sort_keys=True, separators=(',', ':')).encode())
    return encoded + '.' + _encode(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())


def verify(secret, token, audience):
    if len(secret) < 32 or len(token) > 4096:
        raise ValueError('Invalid delegation')
    try:
        encoded, signature = token.split('.')
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _decode(signature)):
            raise ValueError('Invalid signature')
        claims = json.loads(_decode(encoded))
        now = int(time.time())
        if claims['aud'] != audience or not now < claims['exp'] <= now + 7200:
            raise ValueError('Invalid audience or expiry')
        if type(claims['user']) is not int or claims['user'] <= 0:
            raise ValueError('Invalid user')
        for field in ('session', 'run', 'nonce'):
            UUID(claims[field])
        return claims
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid delegation') from exc
