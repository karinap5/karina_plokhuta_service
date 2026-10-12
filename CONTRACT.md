# Контракт API версии 1

Все `/api` требуют HTTP Basic. Учётная запись: demo / demo. Без входа или с неверным паролем — 401, WWW-Authenticate: Basic. Ответы JSON UTF-8; даты ISO 8601 с зоной. Ошибки: {detail:string}, при 422 detail может быть массивом Pydantic. Неизвестные query-поля игнорируются, лишние поля тела создания запрещены.

| Метод и путь | Параметры | Ответ | Ошибки |
|---|---|---|---|
| GET /api/auth | Basic | 200 Account | 401 |
| GET /api/services | нет | 200 {items:Service[]} | 401 |
| GET /api/specialists | service_id≥1 обязательно | 200 {items:Specialist[]} | 401,404,422 |
| GET /api/appointments | page=1≥1,size=20 1..100,status=booked/cancelled?,specialist_id≥1? | 200 Page<ListAppointment> текущего пользователя | 401,422 |
| GET /api/appointments/{id} | id:int | 200 DetailAppointment текущего пользователя | 401,404,422 |
| GET /api/slots | service_id≥1,start,end ISO8601 с зоной,start<end; specialist_id≥1?,page=1,size=20 1..100 | 200 Page<Slot> | 401,404,422 |
| POST /api/appointments | JSON {slot_id:int≥1,service_id:int≥1} | 201 Appointment | 401,404,409,422 |
| POST /api/appointments/{id}/cancel | id:int,без тела | 200 Appointment | 401,404,422 |
| GET /api/summary | start,end ISO8601 с зоной,start<end | 200 Summary по всем данным | 401,422 |

## Форматы

- Account: id:int, username:string.
- Service: id:int, name:string, duration_minutes:int.
- Specialist: id:int, name:string, service_id:int.
- Page<T>: items:T[], total:int, page:int, size:int.
- ListAppointment: id:int, status:string, starts_at:datetime, ends_at:datetime, slot_id:int, specialist_id:int, service_id:int, specialist:string, service:string.
- Appointment: id:int, account_id:int, slot_id:int, specialist_id:int, service_id:int, starts_at:datetime, ends_at:datetime, status:booked|cancelled, cancelled_at:datetime|null.
- DetailAppointment: все Appointment плюс specialist:string,service:string,duration_minutes:int,slot_starts_at:datetime,slot_ends_at:datetime.
- Slot: id:int,specialist_id:int,specialist:string,starts_at:datetime,ends_at:datetime.
- Summary: total:int,cancelled:int,cancelled_percent:number,start:datetime,end:datetime,items:Load[].
- Load: id:int,name:string,available_minutes:number,booked_minutes:number,utilization_percent:number.

## Поведение

Список записей отсортирован по id DESC; слоты — starts_at,id ASC; справочники и сводка — id ASC. Пустая страница: items=[],200. Неизвестный specialist_id фильтрует до пустого списка. Неизвестная услуга при поиске слотов/специалистов даёт 404.

Свободный слот: специалист выполняет услугу, её длительность помещается в слот, ни одна действующая запись не пересекается с интервалом [начало слота,начало+длительность). Начало слота в [start,end). Создание: 404 при отсутствии слота/услуги, 409 при несовместимости/недостаточной длительности/занятом времени. Проверка и вставка идут в одной транзакции с блокировкой специалиста. Отмена сохраняет историю и освобождает время. Повторная отмена — 200 с прежней cancelled_at. Чужая запись — 404.

В сводке total/cancelled считаются по началу записи в [start,end). Загрузка — минуты пересечения действующих записей и расписания с периодом. Проценты округляются до двух знаков; пустой знаменатель — 0.

Заголовки измерения X-Server-Ms,X-DB-Ms,X-App-Ms,X-Connect-Ms,X-Transaction-Ms,X-Query-Count не меняют JSON. Cache-Control: no-store. Диагностический /health без авторизации: 200 {status:"ok",project:"karina_plokhuta"}, вне бизнес-контракта.
