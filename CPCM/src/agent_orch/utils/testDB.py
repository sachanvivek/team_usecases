from DBConnect import DBConnect  
try:
    db = DBConnect() 
    print(f"Connected using DBConnect class")
    db.close()

except Exception as e:
    print(f" Connection failed: {e}")
