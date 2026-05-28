#!/bin/bash
set -e

echo "host replication replicator all scram-sha-256" >> /var/lib/postgresql/data/pg_hba.conf
echo "🛡️ ПРАВИЛО REPLICATION TRUST ИНЖЕКТИРОВАНО В PG_HBA.CONF SUCCESSFUL"
