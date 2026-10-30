# Qarau локально: проход перед LaunchPad

Всё запускается на этом Mac одной командой. Отметки: **работает сейчас** или **придёт с задачей X**
(состояние `dev` на 2026-10-29).

## 1. Запуск — работает сейчас

```bash
cd ~/Documents/Startup/qarau
make local          # или: make local LAN=1 — чтобы открыть с телефона в той же Wi-Fi
```

Поднимает Postgres, Valkey, S3 (docker), миграции, dev-данные, API, воркер и веб-приложение
(production-сборка). Ждёт проверок здоровья и печатает адрес (`http://localhost:4173`), адрес для
телефона, три логина и ссылки на демо. Первый запуск дольше: сборка веба, скачивание образа Valkey.

Где что считается (это же печатает `make local`):
- кадры, sparse-реконструкция (COLMAP), экспорт, покрытие — **CPU этого Mac**;
- плотное облако (dense) приложение **не запускает**: после загрузки будет только sparse-карта.
  Dense на Modal (платный GPU, тратит лимит $30) запускается только вручную командой
  `dense-remote --provider modal --allow-paid-gpu` (ADR 0019). Без `MODAL_TOKEN_ID` и
  `MODAL_TOKEN_SECRET` команда останавливается с текстом «Modal token not set …», ничего не создаётся
  и ничего поддельного не показывается.

## 2. Вход — работает сейчас

Имя `engineer`, пароль — токен engineer из вывода `make local` (он хранится в
`data/local/dev-logins.env`). `admin` и `viewer` входят так же.

Важно: в вашем `.env` пустой `QARAU_API_TOKEN`, поэтому `make local` (без `LAN=1`) работает в
открытом dev-режиме: чтение пускает с **любым** паролем, а загрузка и решения требуют настоящего
токена. Это только для `localhost`.

С `LAN=1` открытого dev-режима нет никогда: если `QARAU_API_TOKEN` пустой, `make local` один раз
создаёт случайный (`QARAU_LAN_API_TOKEN` в `data/local/dev-logins.env`) и запускает API с ним.
Тогда из Wi-Fi входят только три dev-логина и коды событий, любой другой пароль — 401. Вывод
`make local LAN=1` пишет это в строке `Access: closed`. Без `LAN=1` ничего не меняется.

## 3. Демо-инспекция — работает сейчас

Ссылка `Demo:` из вывода (`/inspections/<N>`): объект «Demo site», этапы обработки и 3D-карта
с видео по кадрам.

## 4. 3D-карта — работает сейчас

`http://localhost:4173/viewer/notre-dame-p3e`: вращение, Орбита / Наблюдатель, виды по осям,
шкала кадров.

## 5. Открыть находку — работает сейчас

Нажмите на маркер «1» на карте или откройте `/viewer/notre-dame-p3e?finding=first`. Справа
откроется панель: класс, размер («масштаб не задан»), статус и 3 кадра с обведённой областью.
Панель может грузиться 10–30 с. Это находка из dev-seed, а не настоящий дефект. Часть подписей
в панели пока на английском — исправит **UI-evidence-c**.

## 6. Покрытие — в интерфейсе пока нет

Задачи на показ покрытия в вебе в backlog ещё нет. Покрытие считается автоматически для каждого
нового видео (шаг 10). Посмотреть его можно в PDF (раздел о неосмотренных зонах) или через API:

```bash
source data/local/dev-logins.env
curl -s -H "Authorization: Bearer $QARAU_DEV_ENGINEER_TOKEN" localhost:4173/api/inspections/<N>/coverage
```

Для демо-инспекции ответ `"computed": false`: её прогон старше шага coverage. В списке этапов
новой инспекции шаг coverage показан как «Неизвестный этап»: веб пока не знает это имя.

## 7. Решение по находке (подтвердить / отклонить) — придёт с UI-evidence-b

До неё — только через API:

```bash
curl -s -X POST -H "Authorization: Bearer $QARAU_DEV_ENGINEER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"verdict":"confirm"}' localhost:4173/api/findings/<id>/decisions
```

## 8. Страница отчёта — придёт с UI-report; PDF — работает сейчас

```bash
curl -s -H "Authorization: Bearer $QARAU_DEV_ENGINEER_TOKEN" \
  "localhost:4173/api/inspections/<N>/report.pdf?lang=ru" -o report.pdf && open report.pdf
```

## 9. Код события и вход гостем — API работает сейчас, экран придёт с DEMO1-ui

Для репетиции возьмите имя `rehearsal`, а не `launchpad`: имя события занимается навсегда, purge
его не освобождает.

```bash
cd backend
uv run python -m app.event create --name rehearsal --org "Rehearsal" \
  --expires 2026-11-01T23:59+05:00 --max-guests 10 --runs notre-dame-p3e
# печатает code: XXXXXXXX (показывается один раз)
curl -s -X POST -H 'Content-Type: application/json' -d '{"code":"XXXXXXXX"}' localhost:4173/api/auth/guest
# в ответе "token": "..." — пароль гостя
```

В браузере: «Выйти», затем имя `guest`, пароль — этот token, и откройте
`/viewer/notre-dame-p3e`. Гость видит 3D-карту общего прогона. Главная страница у гостя
показывает «Не удалось загрузить инспекции», маркеров находок нет: полноценный гостевой экран
и ввод кода — в **DEMO1-ui**. После репетиции удалите данные гостей:
`uv run python -m app.event purge --name rehearsal`.

## 10. Загрузить видео — работает сейчас

«Новая инспекция» → объект → файл. Обработка запускается сама, как только загрузка
завершилась; кнопка «Запустить обработку» появляется только у упавшего или остановленного
прогона. Видео обрабатывается на CPU Mac. Проверено через API на
`data/samples/notre_dame_oubiels.webm` (2,9 МБ): около 3,6 мин, из них sparse 202 с. Видео
офиса с телефона (2 мин, 300 кадров) — sparse 631 с (2026-10-30). Ход обработки виден в этапах инспекции, лог —
`data/local/worker.log`.

## 11. Остановка — работает сейчас

```bash
make local-stop
```

Останавливает API, воркер, веб и контейнеры. Данные (docker volumes, `data/runs`) остаются.
