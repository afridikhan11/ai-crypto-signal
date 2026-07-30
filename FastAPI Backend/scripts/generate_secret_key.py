"""
Generates a strong, random SECRET_KEY for production use.

SECRET_KEY is not decorative here - app/security/api_key_cipher.py derives
the encryption key for the saved Binance API credentials from it (see
ApiKeyCipher). Shipping this repo with the default
"change_me_in_production" means anyone with the source code (which is
everyone, once this is published) can decrypt any saved credentials file.

Usage:
    python scripts/generate_secret_key.py

Then paste the output into your .env (or .env.production) as SECRET_KEY.

IMPORTANT: changing SECRET_KEY on an environment that already has saved
Binance credentials (data/binance_credentials.enc) makes that file
undecryptable - you will need to re-enter your API key/secret in Settings
afterwards. This is expected, not a bug: the encryption key changed.
"""
import secrets

if __name__ == "__main__":
    print(secrets.token_urlsafe(48))
