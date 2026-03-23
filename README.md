Установить докер контейнер на сервере за пределами маразма...


### Для локальной разработки

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

### Запуск

#### 1. Создаём .env с реальными значениями

```bash
cp .env.example .env && nano .env
```

#### 1.1 Для https создаем Кадди файл (нужен реальный домен)

```bash
cp Caddyfile.example Caddyfile && nano Caddyfile
```

#### 2. Собираем и запускаем

```bash
docker compose up -d --build
```

#### 3. Проверяем health

```bash
curl http://localhost/health
```