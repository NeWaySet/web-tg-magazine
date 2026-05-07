import base64
import hashlib
import hmac
import json
import logging
import os

from cryptography.fernet import Fernet, InvalidToken


logger = logging.getLogger(__name__)
ENCRYPTED_PREFIX = 'enc:'
_fernet = None


def _derive_fernet_key(value):
    digest = hashlib.sha256(value.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)


def _configured_fernet_key():
    configured_key = (os.getenv('DATA_ENCRYPTION_KEY') or '').strip()
    if configured_key:
        key = configured_key.encode('utf-8')
        try:
            Fernet(key)
            return key
        except Exception:
            logger.warning('DATA_ENCRYPTION_KEY is not a Fernet key; deriving a Fernet key from it.')
            return _derive_fernet_key(configured_key)

    secret_key = os.getenv('SECRET_KEY') or 'dev-secret-key'
    logger.warning('DATA_ENCRYPTION_KEY is not set; deriving data encryption key from SECRET_KEY.')
    return _derive_fernet_key(secret_key)


def get_fernet():
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_configured_fernet_key())
    return _fernet


def encrypt_value(value):
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    if text.startswith(ENCRYPTED_PREFIX):
        return text
    token = get_fernet().encrypt(text.encode('utf-8')).decode('utf-8')
    return f'{ENCRYPTED_PREFIX}{token}'


def decrypt_value(value):
    if value is None:
        return None
    text = str(value)
    if not text:
        return ''
    if not text.startswith(ENCRYPTED_PREFIX):
        return text
    try:
        return get_fernet().decrypt(text[len(ENCRYPTED_PREFIX):].encode('utf-8')).decode('utf-8')
    except InvalidToken:
        logger.error('Encrypted value cannot be decrypted. Check DATA_ENCRYPTION_KEY.')
        return ''


def encrypt_json(value):
    if value is None:
        return None
    return encrypt_value(json.dumps(value, ensure_ascii=False, default=str))


def decrypt_json(value, default=None):
    if not value:
        return default
    decrypted = decrypt_value(value)
    if not decrypted:
        return default
    try:
        return json.loads(decrypted)
    except json.JSONDecodeError:
        logger.error('Encrypted JSON value cannot be decoded.')
        return default


def normalize_email(email):
    return (email or '').strip().lower()


def email_lookup_hash(email):
    normalized_email = normalize_email(email)
    hash_key = (os.getenv('DATA_HASH_KEY') or os.getenv('DATA_ENCRYPTION_KEY') or os.getenv('SECRET_KEY') or 'dev-secret-key')
    return hmac.new(hash_key.encode('utf-8'), normalized_email.encode('utf-8'), hashlib.sha256).hexdigest()


def email_lookup_hashes(email):
    normalized_email = normalize_email(email)
    keys = [
        os.getenv('DATA_HASH_KEY'),
        os.getenv('DATA_ENCRYPTION_KEY'),
        os.getenv('SECRET_KEY'),
        'dev-secret-key'
    ]
    hashes = []
    for key in keys:
        if not key:
            continue
        digest = hmac.new(key.encode('utf-8'), normalized_email.encode('utf-8'), hashlib.sha256).hexdigest()
        if digest not in hashes:
            hashes.append(digest)
    return hashes


def masked_email_value(lookup_hash):
    return f'{ENCRYPTED_PREFIX}{lookup_hash[:48]}'
