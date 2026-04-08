# Sanctions Agent 🛡️

Agent IA qui synchronise automatiquement les listes de sanctions **UN, OFAC et UE**
vers ta base PostgreSQL hébergée sur Render, chaque jour à **18h00 UTC**.

## Architecture

```
sanctions_agent/
├── main.py              ← entry point + APScheduler (18h UTC)
├── config.py            ← env vars
├── core/
│   ├── models.py        ← SanctionEntity, SanctionName, enums
│   └── normalizer.py    ← normalize(), tokenize(), detect_name_type()
├── fetchers/
│   ├── ofac.py          ← OFAC SDN XML parser
│   ├── un.py            ← UN Consolidated XML parser
│   └── eu.py            ← EU FSF XML parser
└── db/
    └── upsert.py        ← upsert entities + entity_names (with ON CONFLICT)
```

## Setup local

```bash
# 1. Clone / copy project
cd sanctions-agent

# 2. Create virtualenv
python -m venv .venv && source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
# → Edit .env : renseigne DATABASE_URL

# 5. Test immédiatement (sans attendre 18h)
python -m sanctions_agent.main --run-now

# 6. Lancer le scheduler en continu
python -m sanctions_agent.main
```

## Déploiement sur Render

### Option A – Background Worker (recommandé)

1. Push le code sur GitHub
2. Dans Render Dashboard → **New Background Worker**
3. Connecte le repo
4. Render détecte `render.yaml` automatiquement
5. Ajoute la variable `DATABASE_URL` (ou utilise **"Connect Database"**)
6. **Deploy** ✅

### Option B – Cron Job Render

Si tu veux un vrai cron Render (payant) :
- Type : **Cron Job**
- Schedule : `0 18 * * *`
- Command : `python -m sanctions_agent.main --run-now`

## Migration DB (première exécution)

Au démarrage, `ensure_schema()` ajoute automatiquement :

```sql
-- Deux colonnes sur la table entities
ALTER TABLE entities ADD COLUMN source_name text;
ALTER TABLE entities ADD COLUMN source_id   text;

-- Index unique pour identifier la source
CREATE UNIQUE INDEX uq_entities_source ON entities (source_name, source_id);
```

> ⚠️ Si tu as déjà des enums PostgreSQL pour `entity_type` et `risk_level`,
> assure-toi que leurs valeurs correspondent à celles dans `core/models.py`.
> Sinon adapte les `EntityType` / `RiskLevel` enums.

## Sources

| Source | URL | Format |
|--------|-----|--------|
| OFAC SDN | https://www.treasury.gov/ofac/downloads/sdn.xml | XML |
| UN SC Consolidated | https://scsanctions.un.org/resources/xml/en/consolidated.xml | XML |
| EU FSF | https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content | XML |

## Adapter les enums PostgreSQL

Si tes enums existants ont des valeurs différentes, modifier dans `core/models.py` :

```python
class EntityType(str, Enum):
    INDIVIDUAL = "person"      # ← mettre la valeur exacte de ton enum PG
    ENTITY     = "organisation"
    ...
```

## Logs attendus

```
2026-04-06 18:00:00 [INFO] – ═══════════ Sanctions sync START ═══════════
2026-04-06 18:00:12 [INFO] – UN – fetched 834 entities
2026-04-06 18:00:31 [INFO] – OFAC – parsed 12043 entities
2026-04-06 18:00:44 [INFO] – EU – parsed 2187 entities
2026-04-06 18:01:10 [INFO] – Total entities to upsert: 15064
2026-04-06 18:03:42 [INFO] – ═ DONE – inserted=15064 updated=0 errors=0 ═
```
# simadou_agent
