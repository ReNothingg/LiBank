# LiBank

<p align="center">
    <img src="static/favicon.ico" alt="Логотип" width="200">
</p>


---

## Как открыть терминал в VSCode (WSL)
> **Нажми:** `Ctrl + ~`

---


## Запуск проекта:
**Создайте файл .env**
```env
ADMIN_PASS1='secret1'
ADMIN_PASS2='secret2'
ADMIN_PASS3='secret3'
SECRET_KEY='change_this_secret'
```

**Использовать виртуальное окружение:**
```bash
sudo apt install python3-venv -y

python3 -m venv venv

source venv/bin/activate

pip install -r requirements.txt
```

**Введите в консоль:**
```bash
source venv/bin/activate
python3 app.py
```

**Перейти по ссылкам:**
* `http://localhost:5000/`
* `http://localhost:5000/admin`