"""
setup_creds.py -- Deriver les credentials API Polymarket depuis la cle privee
Executez UNE FOIS pour obtenir vos API Key/Secret/Passphrase
"""
import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient

load_dotenv()

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon Mainnet

def setup():
    private_key = os.getenv("POLY_PRIVATE_KEY")
    funder = os.getenv("POLY_FUNDER_ADDRESS")

    if not private_key or not funder:
        print("Definissez POLY_PRIVATE_KEY et POLY_FUNDER_ADDRESS dans .env")
        return

    client = ClobClient(
        HOST,
        key=private_key,
        chain_id=CHAIN_ID,
        signature_type=1,
        funder=funder,
    )

    print("Derivation des credentials...")
    try:
        creds = client.create_or_derive_api_creds()
        print("Credentials derives avec succes!")
        print("Ajoutez ces lignes dans votre .env:")
        print("POLY_API_KEY=" + creds.api_key)
        print("POLY_API_SECRET=" + creds.api_secret)
        print("POLY_API_PASSPHRASE=" + creds.api_passphrase)
    except Exception as e:
        print("Erreur: " + str(e))

if __name__ == "__main__":
    setup()