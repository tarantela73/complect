---
title: "Admin User Creation Flow — Temporary Password Generation and Display"
date: 2026-06-24
category: docs/solutions/developer-experience/
module: apps.users
problem_type: developer_experience
component: authentication
severity: medium
applies_when:
  - создание нового пользователя через панель администратора
  - онбординг нового сотрудника — передача временного пароля
  - отладка ситуации «пользователь не может войти» после создания через admin UI
  - добавление email-инвайтов или принудительной смены пароля при первом входе
tags:
  - user-management
  - authentication
  - temp-password
  - admin-panel
  - onboarding
related_components:
  - frontend/src/app/dashboard/admin/page.tsx
  - backend/apps/users/services/user_admin_service.py
  - backend/apps/users/schemas/admin.py
  - backend/apps/users/api_admin.py
---

# Admin User Creation Flow — Temporary Password Generation and Display

## Context

Функция создания пользователей в ERP «Комплект» существует с момента реализации домена `users`, однако рабочий процесс нигде не был задокументирован. Администраторы создавали пользователей методом проб и ошибок, а неочевидное поведение (пароль показывается один раз, письмо не отправляется, логин по email) приводило к ошибкам при онбординге.

## Guidance

### 1. Доступ к странице администрирования

Страница `/dashboard/admin` требует разрешения `users.read`. Кнопка «+ Новый пользователь» отображается только при наличии `users.create`.

### 2. Создание пользователя

Администратор нажимает «+ Новый пользователь» — открывается `CreateUserModal` ([page.tsx:118](frontend/src/app/dashboard/admin/page.tsx#L118)).

Поля формы:
- **Email** — используется как логин (см. ниже почему)
- **Имя** (`full_name`) — отображаемое имя
- **Роли** — чекбоксы из всех доступных ролей тенанта

На submit фронтенд отправляет:

```ts
{ email, full_name, roles, send_invite_email: false }
```

`send_invite_email: false` захардкожен — письмо-приглашение **не отправляется**.

### 3. Backend: генерация временного пароля

`POST /api/v1/admin/users/` вызывает `user_admin_service.create_user()` ([user_admin_service.py:69](backend/apps/users/services/user_admin_service.py#L69)):

```python
# user_admin_service.py — строки 98–108
temp_password = _generate_temp_password()  # 16 символов: upper+lower+digit+!@#$%

user = User(
    username=email,   # username == email — именно поэтому логин происходит по email
    email=email,
    first_name=full_name,
    tenant_id=tenant_id,
)
user.password = make_password(temp_password)  # Argon2; открытый пароль нигде не сохраняется
```

`CreateUserResponse` возвращает `temp_password` в открытом виде ровно **один раз** ([admin.py:29](backend/apps/users/schemas/admin.py#L29)).

### 4. TempPasswordModal — критичный шаг

Фронтенд показывает `TempPasswordModal` ([page.tsx:213](frontend/src/app/dashboard/admin/page.tsx#L213)):

> «Пользователь создан. Сохраните временный пароль — он показывается только один раз.»

Администратор копирует пароль и передаёт его новому пользователю вне системы (устно, мессенджер, и т.д.).

**Если модал закрыт без копирования — восстановить пароль невозможно.** Нет кнопки «показать снова». Единственный выход: удалить пользователя и создать заново.

### 5. Первый вход нового пользователя

```
Логин: ivanov@company.ru    ← email (он же username — UserAdminService устанавливает username=email)
Пароль: xK9#mP2qL7          ← временный пароль от администратора
```

Принудительная смена пароля при первом входе **не реализована** — известный gap.

> **Примечание**: `auth_service.login()` аутентифицирует по полю `username`. Для пользователей, созданных через admin UI, `username=email`, поэтому вход происходит по email. Для `testadmin`, созданного вручную с `username=testadmin`, логин — `testadmin`. Подробности: [dev-login-uses-username-not-email.md](dev-login-uses-username-not-email.md).

## Why This Matters

Три неочевидных детали, вызывающих проблемы при онбординге:

1. **Временный пароль показывается ровно один раз.** После закрытия `TempPasswordModal` `temp_password` недоступен ни через UI, ни через API, ни в БД (хранится только Argon2-хэш).

2. **Email-приглашение не отправляется.** `send_invite_email: false` захардкожен на фронтенде. Если новый пользователь говорит «я не получил письмо» — это ожидаемое поведение, не баг. Точка интеграции для будущего email-invite — флаг уже передаётся в `CreateUserRequest`.

3. **Логин по email, но технически через поле `username`.** `UserAdminService` устанавливает `username=email`. `testadmin` создан иначе (`username=testadmin`), поэтому правила логина отличаются в зависимости от того, как создан пользователь.

## When to Apply

- Онбординг нового сотрудника: объяснить, что пароль нужно скопировать сразу из модала
- Отладка «пользователь не может войти»: проверить, что вводится email, а не имя
- Реализация email-инвайтов: точка входа — `UserAdminService.create_user()`, флаг `send_invite_email` уже в схеме
- Реализация принудительной смены пароля: добавить поле `must_change_password` в `User` и проверку в middleware аутентификации
- Аудит RBAC: `users.create` — создание, `users.read` — просмотр списка, `users.write` — роли и блокировка

## Examples

**Сценарий: онбординг нового менеджера Иванова И.И.**

1. Администратор открывает `/dashboard/admin`
2. Нажимает «+ Новый пользователь»
3. Заполняет: Email `ivanov@company.ru`, Имя `Иванов Иван Иванович`, чекбокс `manager`
4. Нажимает «Создать»
5. Система показывает модал: `Временный пароль: xK9#mP2qL7`
6. Администратор копирует пароль, закрывает модал, отправляет пароль Иванову
7. Иванов входит: логин `ivanov@company.ru`, пароль `xK9#mP2qL7` → успех

**Сценарий ошибки: пароль не скопирован**

- Администратор закрыл `TempPasswordModal` до копирования
- Восстановить `temp_password` нельзя — хранится только Argon2-хэш
- Решение: удалить пользователя, создать заново

## Related

- [dev-login-uses-username-not-email.md](dev-login-uses-username-not-email.md) — почему `testadmin` логинится по `username`, а не email
- [backend/apps/users/services/user_admin_service.py](backend/apps/users/services/user_admin_service.py) — `create_user()`, `_generate_temp_password()`
- [backend/apps/users/schemas/admin.py](backend/apps/users/schemas/admin.py) — `CreateUserRequest` / `CreateUserResponse`
- [frontend/src/app/dashboard/admin/page.tsx](frontend/src/app/dashboard/admin/page.tsx) — `CreateUserModal`, `TempPasswordModal`
