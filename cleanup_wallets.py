import sqlite3

def clean_database():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    
    # Clean api_wallet
    cursor.execute("SELECT id, balance FROM api_wallet")
    wallets = cursor.fetchall()
    updated_wallets = 0
    for w_id, balance in wallets:
        is_valid = True
        if balance is None or str(balance).strip() == '':
            is_valid = False
        else:
            try:
                float(balance)
            except ValueError:
                is_valid = False
        
        if not is_valid:
            cursor.execute("UPDATE api_wallet SET balance = '0.00' WHERE id = ?", (w_id,))
            updated_wallets += 1

    # Clean api_wallettransaction
    cursor.execute("SELECT id, amount FROM api_wallettransaction")
    transactions = cursor.fetchall()
    updated_txs = 0
    for t_id, amount in transactions:
        is_valid = True
        if amount is None or str(amount).strip() == '':
            is_valid = False
        else:
            try:
                float(amount)
            except ValueError:
                is_valid = False
                
        if not is_valid:
            cursor.execute("UPDATE api_wallettransaction SET amount = '0.00' WHERE id = ?", (t_id,))
            updated_txs += 1

    conn.commit()
    print(f"Cleaned up {updated_wallets} wallets and {updated_txs} transactions.")
    conn.close()

if __name__ == "__main__":
    clean_database()
