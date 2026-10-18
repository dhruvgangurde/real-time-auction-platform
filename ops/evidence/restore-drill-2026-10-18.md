<!-- restore-drill result=pass date=2026-10-18 tables=19 rows=11693 checks=6/6 media=474 -->
# Restore drill, 2026-10-18

Result: **pass**. OR-6: a backup taken by the daily backup's own code (`backup` in backend/scripts/backup.sh) was restored into a scratch database with `pg_restore`, its images into a scratch folder, and checked by `python -m app.cli restore-check`; both were deleted afterwards. Made by backend/scripts/restore-drill.sh.

Durations: dump 0.6 s, restore 1.2 s, media 4.8 s.

## Rows per table

19 tables, 11,693 rows. The counts were taken before and after the dump and were equal, so the dump is of one state.

| Table | Before the dump | Restored |
|---|---|---|
| alembic_version | 1 | 1 |
| auction_images | 474 | 474 |
| auctions | 474 | 474 |
| audit_log | 3666 | 3666 |
| bids | 1422 | 1422 |
| categories | 8 | 8 |
| demo_runs | 1 | 1 |
| email_verification_tokens | 729 | 729 |
| notification_preferences | 27 | 27 |
| notifications | 1343 | 1343 |
| password_reset_tokens | 19 | 19 |
| payment_events | 16 | 16 |
| payments | 14 | 14 |
| proxy_bids | 64 | 64 |
| sessions | 1087 | 1087 |
| settlements | 443 | 443 |
| user_roles | 1101 | 1101 |
| users | 781 | 781 |
| watchlist | 23 | 23 |

## The six ledger checks

On the latest verified stress run's lot, `9232e50b-67b7-44af-a1cc-0eb8ed025d9c`, with that run's own acknowledgements, as the application role. Same values as on the live database: yes; as recorded at the run: yes.

| Check | Result | Value |
|---|---|---|
| gapless | pass | `{'n': 808, 'lowest': 1, 'highest': 808, 'missing': []}` |
| distinct | pass | `{'n': 808, 'distinct': 808}` |
| winner | pass | `{'winner': 'Bidder-028d', 'final_price': 4535000, 'sequence_no': 808, 'expected_winner': 'Bidder-028d', 'expected_price': 4535000}` |
| one_settlement | pass | `{'settlements': 1}` |
| closed_once | pass | `{'closes': 1}` |
| acked | pass | `{'acknowledged': 808, 'missing': 0}` |

## Images

474 image files named in the restored database; 474 present in the restored media with the live file's exact bytes (SHA-256). Digest of every key and file hash: `e2b0327595e62e30558c532dc2847007d53fc42a083e0ad32a056e3069b1d3a8`.

