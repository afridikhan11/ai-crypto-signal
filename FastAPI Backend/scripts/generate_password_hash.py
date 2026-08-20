"""
Generates a bcrypt password hash for the env vars this backend reads:
  - ADMIN_PASSWORD_HASH  (general API auth, app/core/security.py)
  - OWNER_PASSWORD_HASH  (Smart AI premium gate, app/core/smartai_auth.py)

Usage:
    python scripts/generate_password_hash.py

Prompts for a password (input hidden) and prints its bcrypt hash to paste
into your .env. The plain password is never stored anywhere - only this
hash is, and only the hash is checked at login time.
"""
import getpass

from passlib.context import CryptContext

if __name__ == "__main__":
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm: ")
    if password != confirm:
        print("Passwords did not match.")
    else:
        print(pwd_context.hash(password))
