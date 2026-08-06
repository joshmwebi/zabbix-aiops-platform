"""Generate an RSA key pair for Snowflake key-pair authentication.

    scripts/generate_keypair.py

Writes an encrypted private key to keys/snowflake_key.p8 and prints the
public key in the single-line form Snowflake expects.

Why key pairs: browser-based SSO cannot run unattended (no browser at 3am,
nobody to click) and is subject to device-based Conditional Access policies.
Key-pair auth is machine-to-machine — the connector signs a JWT with the
private key and Snowflake verifies it against the registered public key. No
browser, no identity provider in the path.

Uses the `cryptography` library, which ships as a dependency of
snowflake-connector-python, so nothing extra to install and no OpenSSL
binary needed on Windows.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

REPO_ROOT = Path(__file__).resolve().parent.parent
KEY_DIR = REPO_ROOT / "keys"
PRIVATE_KEY = KEY_DIR / "snowflake_key.p8"
PUBLIC_KEY = KEY_DIR / "snowflake_key.pub"


def main() -> int:
    if PRIVATE_KEY.exists():
        print(f"{PRIVATE_KEY} already exists. Delete it first if you intend to")
        print("rotate the key — a new key invalidates the one registered in Snowflake.")
        return 1

    KEY_DIR.mkdir(exist_ok=True)

    print("Passphrase encrypts the private key at rest. Leave blank for none.")
    print("If you set one, it goes in .env as SNOWFLAKE_PRIVATE_KEY_PASSPHRASE.\n")
    passphrase = getpass.getpass("Passphrase (blank for unencrypted): ")

    # 2048 is Snowflake's documented minimum and is sufficient here.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    encryption = (
        serialization.BestAvailableEncryption(passphrase.encode())
        if passphrase
        else serialization.NoEncryption()
    )

    # PKCS#8 PEM is the format the connector expects.
    PRIVATE_KEY.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=encryption,
        )
    )

    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    PUBLIC_KEY.write_bytes(pub_pem)

    # Snowflake wants the base64 body only — no BEGIN/END lines, no newlines.
    body = "".join(
        line for line in pub_pem.decode().splitlines() if not line.startswith("-----")
    )

    print(f"\nPrivate key: {PRIVATE_KEY}")
    print(f"Public key:  {PUBLIC_KEY}")
    print("\nRegister this public key on the Snowflake user. Either paste it into")
    print("the portal's technical-user form, or have someone with USERADMIN run:\n")
    print(f"  ALTER USER <TECHNICAL_USER> SET RSA_PUBLIC_KEY='{body}';\n")
    print("Then in .env:")
    print(f"  SNOWFLAKE_PRIVATE_KEY_PATH={PRIVATE_KEY}")
    if passphrase:
        print("  SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=<the passphrase you just set>")
    print("  SNOWFLAKE_AUTHENTICATOR=        (leave blank — key pair replaces SSO)")
    print("\nThe keys/ directory is gitignored. The private key must never be")
    print("committed, shared, or copied off this machine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
