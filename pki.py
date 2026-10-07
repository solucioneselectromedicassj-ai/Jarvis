"""PKI propia con la biblioteca `cryptography` (sin el programa openssl: evita los problemas de rutas en Windows).

Los certificados son válidos desde 1969: los ESP32 arrancan con la hora en 1970.
"""
import ipaddress
import os
import subprocess
from datetime import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

VALID_FROM = datetime(1969, 12, 31, 0, 0, 0)
VALID_TO = datetime(2049, 12, 31, 23, 59, 59)  # límite de UTCTime


def restringir(path: Path) -> None:
    """Deja el archivo legible solo por el dueño (Linux) o por Administradores y SYSTEM (Windows)."""
    if os.name == "nt":
        # SIDs y no nombres: en Windows en español el grupo se llama "Administradores"
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", "*S-1-5-32-544:F", "/grant:r", "*S-1-5-18:F"],
            check=True, capture_output=True,
        )
    else:
        os.chmod(path, 0o600)


def _nombre(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _guardar_clave(key, path: Path) -> None:
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    restringir(path)


def crear_ca(dir_: Path) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    nombre = _nombre("Casa CA")
    cert = (
        x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(VALID_FROM).not_valid_after(VALID_TO)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .sign(key, hashes.SHA256())
    )
    _guardar_clave(key, dir_ / "ca.key")
    (dir_ / "ca.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def emitir_servidor(dir_: Path, ips: list[str], dnss: list[str]) -> None:
    ca_key = serialization.load_pem_private_key((dir_ / "ca.key").read_bytes(), None)
    ca_cert = x509.load_pem_x509_certificate((dir_ / "ca.crt").read_bytes())
    sans = [x509.IPAddress(ipaddress.ip_address(i)) for i in ips] + [x509.DNSName(d) for d in dnss]
    if not sans:
        raise ValueError("Hace falta al menos una IP o un nombre para el certificado del servidor")
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder().subject_name(_nombre("Casa servidor")).issuer_name(ca_cert.subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(VALID_FROM).not_valid_after(VALID_TO)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _guardar_clave(key, dir_ / "servidor.key")
    (dir_ / "servidor.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
