# Sanctions Agent

Agent qui synchronise quotidiennement les listes de sanctions
**UN, OFAC et UE** vers une base PostgreSQL hébergée sur Render,
chaque jour à **18:00 UTC**.

Stratégie : **wipe & reload par source**. À chaque run, pour chaque
source qui a répondu correctement, on supprime toutes les lignes
existantes pour ce `source_name` puis on insère le batch frais. Chaque
source est traitée dans une transaction isolée (un échec sur une source
n'affecte pas les autres).

## Architecture

```
sanctions_agent/
├── main.py          ← entry point + APScheduler (mode --run-now ou daemon)
├── config.py        ← env vars + URLs des sources
├── models.py        ← SanctionEntity, SanctionName, enums
├── normalizer.py    ← normalize / tokenize / detect_name_type
├── http_client.py   ← retry + backoff exponentiel + UA navigateur
├── un.py            ← parser UN Consolidated XML
├── ofac.py          ← parser OFAC SDN XML
├── eu.py            ← parser EU FSF XML (avec namespace)
├── delta.py         ← hash store (read-only stats + commit_hashes)
├── upsert.py        ← wipe-by-source + ensure_schema + FK auto-migration
└── notifier.py      ← Brevo SMTP + Slack optionnel
```

## Pipeline d'un run

1. **Fetch** UN / OFAC / EU XML (avec retry + UA navigateur).
2. **Parse** → `SanctionEntity` objects.
3. **compute_delta** (lecture seule) → stats `new / modified / unchanged / removed`
   pour le rapport, en comparant avec `sanctions_sync_meta`.
4. **wipe_and_insert** : pour chaque source, dans une transaction :
   - `DELETE FROM entity_names WHERE entity_id IN (SELECT id FROM entities WHERE source_name = 'X')`
   - `DELETE FROM entities WHERE source_name = 'X'`
   - `INSERT` du batch frais (UUIDs neufs)
5. **commit_hashes** → upsert dans `sanctions_sync_meta` pour le delta du prochain run (non-fatal).
6. **notify** → envoi email Brevo SMTP (succès ou partiel) + Slack si configuré.

## Setup local

```bash
cd sanctions_agent

python -m venv .venv && source .venv/bin/activate
pip install -r sanctions_agent/requirements.txt

cp .env.example .env
# → renseigne au moins DATABASE_URL (avec ?sslmode=require pour Render externe)

# Run unique
python -m sanctions_agent.main --run-now

# Mode daemon (scheduler embarqué)
python -m sanctions_agent.main
```

> Pour tester en local contre la DB Render, il faut whitelister ton IP
> publique dans **Render → DB → Access Control** (`/32`). Sur Render, le
> Cron Job tourne dans le réseau privé, aucune whitelist nécessaire.

## Déploiement sur Render

Type **Cron Job** natif (scale-to-zero, pas de daemon qui dort).
`render.yaml` à la racine du repo détecté automatiquement :

```yaml
type: cron
schedule: "0 18 * * *"
buildCommand: pip install -r sanctions_agent/requirements.txt
startCommand: python -m sanctions_agent.main --run-now
```

### Variables d'environnement à configurer

| Variable           | Source            | Obligatoire | Notes                                      |
| ------------------ | ----------------- | ----------- | ------------------------------------------ |
| `DATABASE_URL`     | `fromDatabase`    | oui         | Auto-injecté depuis le service DB Render   |
| `PYTHON_VERSION`   | `render.yaml`     | oui         | Pinné à `3.11.10`                          |
| `LOG_LEVEL`        | `render.yaml`     | non         | `INFO` par défaut                          |
| `BREVO_SMTP_LOGIN` | dashboard manuel  | recommandé  | Email du compte Brevo                      |
| `BREVO_SMTP_KEY`   | dashboard manuel  | recommandé  | SMTP key Brevo (`xsmtpsib-…`)              |
| `BREVO_SMTP_HOST`  | env (default)     | non         | `smtp-relay.brevo.com`                     |
| `BREVO_SMTP_PORT`  | env (default)     | non         | `587`                                      |
| `BREVO_FROM_EMAIL` | dashboard manuel  | recommandé  | Adresse vérifiée dans Brevo                |
| `BREVO_FROM_NAME`  | dashboard manuel  | non         | `Sanctions Agent` par défaut               |
| `BREVO_TO_EMAIL`   | dashboard manuel  | recommandé  | Destinataire(s), séparés par `,`           |
| `SLACK_WEBHOOK_URL`| dashboard manuel  | optionnel   | Si présent, envoie aussi un message Slack  |

L'auto-deploy n'est pas activé par défaut sur les Cron Jobs Render —
à activer dans **Settings → Build & Deploy** si tu veux que chaque
push redéploie automatiquement.

## Migrations DB automatiques

Au démarrage, `ensure_schema()` exécute (idempotent) :

```sql
-- Colonnes pour identifier la source
ALTER TABLE entities ADD COLUMN source_name text;
ALTER TABLE entities ADD COLUMN source_id   text;

-- Index pour le wipe-by-source
CREATE INDEX        ix_entities_source_name ON entities (source_name);
CREATE UNIQUE INDEX uq_entities_source      ON entities (source_name, source_id);

-- Promotion des FK pointant sur entities.id en ON DELETE CASCADE
-- (sinon le wipe planterait sur RESTRICT/NO ACTION)
```

`ensure_delta_schema()` ajoute en plus :

```sql
CREATE TABLE sanctions_sync_meta (
    source_id   text PRIMARY KEY,
    source_name text NOT NULL,
    content_hash text NOT NULL,
    last_seen   timestamptz NOT NULL DEFAULT now()
);
```

## Sources

| Source             | URL                                                                                          | Format |
| ------------------ | -------------------------------------------------------------------------------------------- | ------ |
| OFAC SDN           | `https://www.treasury.gov/ofac/downloads/sdn.xml`                                            | XML    |
| UN SC Consolidated | `https://scsanctions.un.org/resources/xml/en/consolidated.xml`                               | XML    |
| EU FSF             | `https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=…` | XML    |

Le endpoint EU FSF exige un token `?token=dG9rZW4tMjAxNw` (token public,
voir [opensanctions.org/datasets/eu_fsf](https://www.opensanctions.org/datasets/eu_fsf/)),
sinon il renvoie 403.

## Vérifier l'intégrité après un run

```sql
-- Comptes attendus (~1009 UN, ~18900 OFAC, ~6000 EU au moment d'écrire)
SELECT source_name, COUNT(*) AS total, COUNT(DISTINCT source_id) AS uniques
FROM   entities
WHERE  source_name IN ('UN','OFAC','EU')
GROUP  BY source_name;

-- Détection de doublons (doit retourner 0 ligne)
SELECT source_name, source_id, COUNT(*)
FROM   entities
WHERE  source_name IN ('UN','OFAC','EU')
GROUP  BY source_name, source_id
HAVING COUNT(*) > 1;
```

## Logs attendus (run réussi)

```
Sanctions Agent starting – checking DB schema …
DB – schema checks done
FK check – screening_matches.entity_id -> entities.id (ON DELETE CASCADE)
…
Delta schema OK
══════════════ Sanctions sync START ══════════════
UN   – 1009  entities fetched
OFAC – 18927 entities fetched
EU   – 6002  entities fetched
Total entities fetched: 25938
Delta (stats only) – new=… modified=… unchanged=… removed=…
UN   – wiped 1009,  inserted 1009
OFAC – wiped 18927, inserted 18927
EU   – wiped 6002,  inserted 6002
══ DONE (Xs) inserted=25938 sources_wiped=3 errors=0 ══
Brevo SMTP email sent to ['recipient@example.com']
```
