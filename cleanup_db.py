import os
import sys

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cubelogs.settings')
import django
django.setup()

from api.models import Wallet, WalletTransaction
from decimal import Decimal, InvalidOperation

def cleanup_corrupted_decimals():
    print("Cleaning up corrupted wallet transactions...")
    deleted_tx = 0
    # Fetching all without filtering by amount to avoid SQLite casting crash
    for tx in WalletTransaction.objects.all():
        try:
            if not tx.amount or str(tx.amount).strip() == '':
                tx.delete()
                deleted_tx += 1
                continue
            Decimal(str(tx.amount))
        except (InvalidOperation, ValueError, TypeError):
            tx.delete()
            deleted_tx += 1

    print(f"Deleted {deleted_tx} corrupted WalletTransaction records.")

    print("Cleaning up corrupted wallets...")
    deleted_wallets = 0
    for w in Wallet.objects.all():
        try:
            if not w.balance or str(w.balance).strip() == '':
                w.delete()
                deleted_wallets += 1
                continue
            Decimal(str(w.balance))
        except (InvalidOperation, ValueError, TypeError):
            w.delete()
            deleted_wallets += 1

    print(f"Deleted {deleted_wallets} corrupted Wallet records.")

if __name__ == '__main__':
    cleanup_corrupted_decimals()
