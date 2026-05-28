-- Создание выделенного системного пользователя для стриминга WAL-логов
CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replica_password_secure_2026';
