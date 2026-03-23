Установить докер контейнер на сервере за пределами маразма...

Установка через классический pip

Создать venv - `python -m venv venv`

`pip install -r requirements.txt`

Установка через быстрый uv

`pip install uv==0.6.14`

```bash
uv venv venv --python 3.10
source venv/bin/activate
uv pip install -r requirements.txt
```
