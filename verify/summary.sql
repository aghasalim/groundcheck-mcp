-- The published case summary, recomputed from the raw case tables in SQLite.
--
-- The README publishes a table of how many cases each file holds and how they
-- split across the three verdicts. Those counts were read off the same tables
-- by the same person who wrote them, so nothing checked that the summary in
-- the prose is the summary in the data. This recomputes it with a group by and
-- the harness diffs the two, so a case that is added, dropped or relabelled
-- and not written up is a failure rather than a quiet inconsistency.
--
-- It also asserts the invariants the tables are supposed to satisfy. Any row
-- printed with a "bad:" prefix is a violation and fails the harness.
--
--     sqlite3 -init verify/summary.sql :memory: ""
--
-- Run from the repository root: the .import paths are relative to it.
.bail on
.mode tabs

.import verify/cases_math.tsv math
.import verify/cases_repo.tsv repo
.import verify/cases_text.tsv text

.headers off
.mode list
.separator " "

-- The summary the README publishes, one row per table.
SELECT 'cases_math.tsv', count(*),
       sum(status = 'checked'), sum(status = 'refuted'), sum(status = 'unverifiable')
FROM math
UNION ALL
SELECT 'cases_repo.tsv', count(*),
       sum(status = 'checked'), sum(status = 'refuted'), sum(status = 'unverifiable')
FROM repo
UNION ALL
SELECT 'cases_text.tsv', count(*),
       sum(status = 'checked'), sum(status = 'refuted'), sum(status = 'unverifiable')
FROM text;

-- Only the three documented verdicts exist. A fourth would mean the contract
-- in the README no longer describes what the tools return.
SELECT 'bad: ' || t || ' row ' || id || ' has status ' || status FROM (
    SELECT 'math' AS t, id, status FROM math
    UNION ALL SELECT 'repo', id, status FROM repo
    UNION ALL SELECT 'text', id, status FROM text
) WHERE status NOT IN ('checked', 'refuted', 'unverifiable');

-- Ids identify a case, so two rows may not share one.
SELECT 'bad: duplicate id ' || id FROM (
    SELECT id FROM math UNION ALL SELECT id FROM repo UNION ALL SELECT id FROM text
) GROUP BY id HAVING count(*) > 1;

-- check_math records a computed value exactly when it reached a verdict, and
-- that value has to be a number.
SELECT 'bad: math row ' || id || ' is ' || status || ' with value ' || quote(value)
FROM math
WHERE (status = 'unverifiable') <> (value = '')
   OR (value <> '' AND value NOT GLOB '[-+0-9]*');

-- check_repo returns hits when and only when it found something, and never
-- more than the documented cap of five.
SELECT 'bad: repo row ' || id || ' is ' || status || ' with ' || hits || ' hits'
FROM repo
WHERE CAST(hits AS INTEGER) > 5
   OR (status = 'checked') <> (CAST(hits AS INTEGER) > 0);
