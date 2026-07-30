"""
Generates a bcrypt hash for the admin password, for ADMIN_PASSWORD_HASH.

Usage:
    python scripts/generate_password_hash.py

Prompts for a password (input hidden), prints the hash to paste into
.env.production as ADMIN_PASSWORD_HASH. The plain password is never
stored anywhere - only this hash is, and only the hash is checked at
login time (see app/core/security.py verify_password).
"""
import getpass

from passlib.context import CryptContext

if __name__ == "__main__":
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm: ")
    if password != confirm:
        print("Passwords did not match.")
    else:
        print(pwd_context.hash(password))
