# LiBank

# Как открыть терминал в VSCode (WSL)
> **Нажми:** `Ctrl + ~`

# Использовать виртуальное окружение:
```bash
sudo apt install python3-venv -y

python3 -m venv venv

source venv/bin/activate

pip install -r requirements.txt
```

# Запуск проекта:
```bash
source venv/bin/activate
python3 app.py
```

**Затем перейти по ссылке:**
* `http://localhost:5000/`
* `http://localhost:5000/admin`

**Создайте файл .env**
```env
ADMIN_PASS1='secret1'
ADMIN_PASS2='secret2'
ADMIN_PASS3='secret3'
SECRET_KEY='change_this_secret'
```