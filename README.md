# Urban Energy Web3 Audit (TRL 4–5)

Прототип гибридной блокчейн-платформы для мониторинга, независимого аудита и стимулирования энергосбережения в городской среде. Система объединяет криптографическую фиксацию телеметрии умных счетчиков, ML-аналитику выявления неэффективностей и токенизированную мотивацию потребителей.

## 1. Архитектура и технологический стек

Платформа построена по **гибридной Web3-архитектуре**: критичные метаданные, криптографические хеши, аудит-логи и токенизированные стимулы хранятся **on-chain**, а массивы сырых показаний и метаданные сертификатов — **off-chain** (IPFS + PostgreSQL).

| Слой системы | Технологии | Назначение |
| :--- | :--- | :--- |
| **Смарт-контракты (On-chain)** | Hardhat 3, Solidity `0.8.28`, OpenZeppelin Contracts v5, TypeScript | Реестр устройств, RBAC, проверка ECDSA-подписей, аудит-трасса, эмиссия ERC-20 и ERC-721 |
| **Off-chain хранилища** | PostgreSQL 16, IPFS Kubo (Docker) | Индексация рядов, хранение сырых JSON-пакетов телеметрии и метаданных NFT |
| **IoT-транспорт и симуляция** | Eclipse Mosquitto (MQTT), Python 3.12, `paho-mqtt`, `eth-account` | Эмуляция умных счетчиков для 4 типов городских объектов с подписью каждого пакета |
| **Бэкенд и ML-аналитика** | FastAPI, `web3.py` v7, `psycopg` v3, `scikit-learn`, `pandas`, `numpy` | Прием MQTT-потока, загрузка в IPFS, отправка транзакций, пороговые алерты, K-means, Isolation Forest |
| **Веб-дашборд (Frontend)** | React, Vite, `ethers.js` v6, Recharts | Визуализация графиков, кластеров, рейтингов, балансов токенов и независимый Web3 Audit Explorer |
| **Аудит безопасности** | Slither Analyzer, `solc-select` (Docker) | Статический анализ смарт-контрактов на уязвимости (reentrancy, арифметика, контроль доступа) |

### Состав смарт-контрактов

* **`EnergyAuditSystem.sol`** — ядро платформы: регистрация устройств (`registerDevice`), фиксация подписанных показаний с защитой от повтора (`submitReading`), расчет бонусов и штрафов за период (`settlePeriod`: $10\%$ экономии $= 100\text{ ECOT}$) и неизменяемая аудит-трасса (`getDeviceReadings`).
* **`EcoToken.sol` (ERC-20)** — утилитарный токен поощрения (`ECOT`) за снижение потребления ниже базового уровня.
* **`EcoCertificate.sol` (ERC-721)** — цифровой NFT-сертификат энергоэффективности (`ECOCERT`), выпускаемый при экономии $\ge 10\%$.

## 2. Структура репозитория

```text
energy-web3-audit/
├── contracts/                  # Hardhat 3, Solidity-контракты, unit-тесты, скрипты деплоя
│   ├── contracts/
│   │   ├── EcoToken.sol
│   │   ├── EcoCertificate.sol
│   │   └── EnergyAuditSystem.sol
│   ├── scripts/deploy.ts
│   ├── test/EnergyAuditSystem.test.ts
│   └── hardhat.config.ts
├── backend/                    # FastAPI-агрегатор, Web3/IPFS/DB клиенты, ML-аналитика
│   ├── main.py
│   ├── blockchain.py
│   ├── database.py
│   ├── ipfs_client.py
│   ├── analytics.py
│   ├── requirements.txt
│   └── Dockerfile
├── iot-simulator/              # Python-симуляторы IoT и бенчмарки
│   ├── simulator.py            # Базовый тест (1 счетчик)
│   ├── microgrid_sim.py        # Симуляция городской микро-сети TRL 5 (20 объектов)
│   └── stress_benchmark.py     # Стресс-тест, масштаб TRL 4 (500 устр.) и оценка эффекта стимулов
├── frontend/                   # Веб-дашборд (React + Vite + ethers.js)
├── infra/mosquitto/config/     # Конфигурация MQTT-брокера Mosquitto
├── .env                        # Переменные окружения
├── docker-compose.yml          # PostgreSQL, Mosquitto, IPFS, Backend
└── README.md
```

## 3. Предварительные требования

* **Docker Desktop** (с поддержкой `docker compose`)
* **Node.js** `v20+` (LTS) и `npm`

> Python-бэкенд и симуляторы запускаются внутри Linux-контейнера `energy_backend`, что гарантирует воспроизводимость окружения и исключает блокировку нативных DLL в Windows.

## 4. Пошаговый запуск проекта

### Шаг 1. Поднять базовую инфраструктуру (PostgreSQL, MQTT, IPFS)

Из корневой директории `energy-web3-audit`:

```powershell
docker compose up -d postgres mosquitto ipfs
docker compose ps
```

### Шаг 2. Запустить локальную ноду Hardhat 3 и задеплоить контракты

В **Терминале 1** запустите локальную Ethereum-ноду (`http://127.0.0.1:8545`, Chain ID `31337`):

```powershell
cd contracts
npm install
npx hardhat node
```

В **Терминале 2** выполните сборку, unit-тесты с отчетом по газу и деплой:

```powershell
cd contracts
npx hardhat build
npx hardhat test --gas-stats
npx hardhat run scripts/deploy.ts --network localhost
```

После деплоя в папке `contracts/` автоматически создастся файл `deployment.json` с адресами контрактов.

### Шаг 3. Запустить FastAPI-бэкенд (агрегатор + ML)

Из корневой директории проекта:

```powershell
docker compose up -d --build backend
docker logs -f energy_backend
```

* Swagger UI API: `http://127.0.0.1:8000/docs`
* Проверка статуса: `http://127.0.0.1:8000/health`

### Шаг 4. Запустить симуляцию городской микро-сети (TRL 5) и стресс-тест

1. **Симуляция 20 городских объектов** ($10$ жилых домов, $4$ офиса, $4$ общественных объекта, $2$ линии уличного освещения + выпуск ECOT и NFT):

   ```powershell
   docker exec -it energy_backend python /app/iot-simulator/microgrid_sim.py
   ```

2. **Нагрузочный бенчмарк TRL 4–5** (стресс-тест on-chain, $500$ устройств / $10\,000$ записей, двухфазная оценка стимулов):

   ```powershell
   docker exec -it energy_backend python /app/iot-simulator/stress_benchmark.py
   ```

### Шаг 5. Запустить веб-дашборд (React + `ethers.js`)

В отдельном терминале:

```powershell
cd frontend
npm install
npm run dev
```

Откройте дашборд в браузере по адресу `http://localhost:5173`.

## 5. Аудит безопасности смарт-контрактов (Slither)

Проверка пользовательских контрактов (`EcoToken.sol`, `EcoCertificate.sol`, `EnergyAuditSystem.sol`) без шума от библиотек OpenZeppelin:

```powershell
docker run --rm -v "${PWD}\contracts:/src" -w /src python:3.12-slim bash -c "pip install -q slither-analyzer solc-select && solc-select install 0.8.34 && solc-select use 0.8.34 && slither contracts/ --solc-remaps '@openzeppelin/=node_modules/@openzeppelin/' --filter-paths 'node_modules' --print human-summary"
```

### Итоги статического анализа Slither (после устранения `divide-before-multiply`)

| Контракт | Стандарты | High | Medium | Low | Informational | Optimization | Assembly (свой код) | Complex code |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`EcoToken.sol`** | ERC165, ERC20 | $0$ | $0$ | $0$ | $0$ | $0$ | $0$ | No |
| **`EcoCertificate.sol`** | ERC165, ERC721 | $0$ | $0$ | $0$ | $0$ | $0$ | $0$ | No |
| **`EnergyAuditSystem.sol`** | ERC165 | $0$ | $0$ | $0$ | $0$ | $0$ | $0$ | No |

## 6. Результаты валидации (TRL 4–5)

### Сводная таблица соответствия требованиям ТЗ

| Метрика | Требование ТЗ | Полученный результат | Статус |
| :--- | :--- | :--- | :--- |
| **Газоэффективность `submitReading`** | $< 150\,000\text{ gas}$ | $96\,379\text{ gas}$ (среднее) / $111\,776\text{ gas}$ (макс.) | Выполнено |
| **Скорость фиксации транзакции** | $< 5\text{ сек}$ | $27{,}19\text{ мс}$ (среднее) / $55{,}55\text{ мс}$ (макс.) | Выполнено |
| **Пропускная способность (Stress-тест)** | $\ge 2\,000\text{ tx/час}$ | $110\,650\text{ tx/час}$ ($200\text{ tx}$ за $6{,}51\text{ сек}$) | Выполнено |
| **Масштаб симуляции (TRL 4)** | $500\text{--}1\,000$ устройств, $10\,000+$ записей | $500$ устройств, $10\,000$ записей за $159{,}07\text{ сек}$ ($100\%$ ECDSA) | Выполнено |
| **Точность выявления неэффективностей** | $\ge 92\%$ | $100{,}0\%$ (пики $> 150\%$, утечки $> 5\%$, Isolation Forest) | Выполнено |
| **Снижение потребления (эффект стимулов)** | $12\text{--}20\%$ | $15{,}51\%$ ($324{,}37 \rightarrow 274{,}07\text{ кВт}\cdot\text{ч}$; экономия $27{,}66\text{ кг }\text{CO}_2$) | Выполнено |
| **Скорость аудита (Traceability)** | $< 10\text{ сек}$ | $24{,}66\text{ мс}$ ($100\%$ сверка On-chain и Off-chain) | Выполнено |
| **Аудит безопасности (Slither)** | Отсутствие критических уязвимостей | $0$ High, $0$ Medium, $0$ Low во всех $3$ контрактах | Выполнено |

### Показатели прогона городской микро-сети (TRL 5)

* **Объектов в модели:** $20$ ($10$ жилых домов, $4$ офиса, $4$ общественных объекта, $2$ линии уличного освещения).
* **Зафиксировано показаний в блокчейне и IPFS:** $125$ записей (средний газ в микро-сети — $97\,542\text{ gas}$).
* **Суммарное учтенное потребление:** $309{,}462\text{ кВт}\cdot\text{ч}$ ($170{,}204\text{ кг }\text{CO}_2$-эквивалента).
* **Выпущено стимулов смарт-контрактом:** $2\,632{,}0\text{ ECOT}$ (ERC-20) и $12$ NFT-сертификатов (ERC-721).

## 7. Основные API-эндпоинты бэкенда

* `GET /health` — статус подключения к блокчейну, адреса контрактов и счетчик записей.
* `POST /api/devices/register` — регистрация умного счетчика on-chain и в БД.
* `GET /api/devices` — список объектов с балансами `ECOT`, числом NFT и штрафами.
* `GET /api/analytics/overview` — сводка потребления, $\text{CO}_2$, кластеры K-means, ML-аномалии и рекомендации.
* `GET /api/anomalies` — лента пороговых алертов в реальном времени.
* `GET /api/audit/{device_address}` — сверка записей смарт-контракта и off-chain индекса (IPFS CID) с замером времени аудита.
* `POST /api/settle-all` — расчет бонусов/штрафов и выпуск токенов/сертификатов за период.