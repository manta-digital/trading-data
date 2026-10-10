---
docType: notes
slice: tick-restore-drill
project: trading-data
dateCreated: 20261009
dateUpdated: 20261009
---

# Tick restore drill (slice 228)

Started 2026-10-09T21:21:57-06:00.

## Step 0: prepare — PASS

Expected: drill lock held; sudo primed; leftovers cleared; free space >= 3x footprint

Seen:

```
drill lock held: /data/restore-test/.228-drill.lock
leftovers removed: none
free space: need 3 x 964733640 bytes (archive + base 20261005), seen 1332634914816 free on /data/restore-test
drill directory: /data/restore-test/228-drill-20261010T032157Z
```

## Step 1: hold production — PASS

Expected: both tick locks taken; SELECT on every public table; settings read; live archive equals the latest snapshot

Seen:

```
tick advisory lock 220000001 taken: expected True, seen True
tick advisory lock 220000002 taken: expected True, seen True
public tables without SELECT: expected [], seen []
settings read: expected ['max_connections', 'max_locks_per_transaction', 'max_prepared_transactions', 'max_wal_senders', 'max_worker_processes'], seen ['max_connections', 'max_locks_per_transaction', 'max_prepared_transactions', 'max_wal_senders', 'max_worker_processes']
production settings: {'max_connections': '30', 'max_locks_per_transaction': '64', 'max_prepared_transactions': '0', 'max_wal_senders': '10', 'max_worker_processes': '8'}
live archive backed-up set: 176 files, 523349320 bytes, newest 2026-09-30T03:14:15.739061+00:00
snapshot 2026-10-09T04:00:10.607298-06:00: 176 files, 523349320 bytes
```

## Step 2: restore point — PASS

Expected: the switched segment is archived within the bound

Seen:

```
segment the restore must reach: 000000010000002900000082
archived: /data/backup/17-tick/wal/000000010000002900000082.zst
```

## Step 3: restore archive — PASS

Expected: restored files and bytes equal the live archive's backed-up set

Seen:

```
chown -R 1000:1000 /data/restore-test/228-drill-20261010T032157Z/restic-restore/data/tick-archive exit: expected 0, seen 0
restored files: expected 176, seen 176
restored bytes: expected 523349320, seen 523349320
```

## Step 4: restore database — PASS

Expected: base verified; auto.conf emptied; recovery reached step 2's segment

Seen:

```
tar -xzf base.tar.gz exit: expected 0, seen 0
tar -xzf pg_wal.tar.gz exit: expected 0, seen 0
pg_verifybackup exit: expected 0, seen 0
backup successfully verified
postgresql.auto.conf emptied; no archive setting left
last restored segment: 000000010000002900000082; needed 000000010000002900000082
```

## Step 5: compare with production — PASS

Expected: row counts, tick_trade fingerprint, bookkeeping md5 equal; locks held

Seen:

```
row counts: expected {'schema_migrations': 8, 'tick_archive_unit': 156, 'tick_dataset_edge': 1, 'tick_day_condition': 106, 'tick_definition': 51, 'tick_ingest_ledger': 5145, 'tick_request': 6, 'tick_trade': 27691412}, seen {'schema_migrations': 8, 'tick_archive_unit': 156, 'tick_dataset_edge': 1, 'tick_day_condition': 106, 'tick_definition': 51, 'tick_ingest_ledger': 5145, 'tick_request': 6, 'tick_trade': 27691412}
tick_trade fingerprint differences: expected [], seen []
tick_request md5: expected 1d918199bf5e8679007c884292e34977, seen 1d918199bf5e8679007c884292e34977
tick_archive_unit md5: expected 90be7786b57f88593443b29cacd0a380, seen 90be7786b57f88593443b29cacd0a380
tick_definition md5: expected 643bdc13a33e7c1c5f8448c60abb49b2, seen 643bdc13a33e7c1c5f8448c60abb49b2
tick_ingest_ledger md5: expected 8f03df8cde4ecc9c4a530b67bbee7985, seen 8f03df8cde4ecc9c4a530b67bbee7985
tick_dataset_edge md5: expected 8e39892a334a943236f295740c8e76bf, seen 8e39892a334a943236f295740c8e76bf
tick_day_condition md5: expected 46552b8e06691cf5a36c15f32d04163c, seen 46552b8e06691cf5a36c15f32d04163c
tick advisory locks still held: expected 2, seen 2
locks released 2026-10-10T03:23:57+00:00
```

## Step 6: rebuild from the restored archive — PASS

Expected: provision, migrate, adopt per job, pass --estimate-only, ingest exit 0

Seen:

```
provision trading_tick_drill exit: expected 0, seen 0
jobs to adopt, in order: ['GLBX-20240930-USM7UXXJBA', 'GLBX-20250123-XT4GD5UM6C', 'GLBX-20260930-DLDYL5DM8Q', 'GLBX-20260930-HVGRLYKHRN', 'GLBX-20260930-MBERAR6R7T', 'GLBX-20260930-VDPHT5ESUC']
$ mt data migrate apply --track tick  (exit 0, 1.0 s)
$ mt data tick adopt --job-id GLBX-20240930-USM7UXXJBA --source /data/restore-test/228-drill-20261010T032157Z/restic-restore/data/tick-archive/GLBX-20240930-USM7UXXJBA  (exit 0, 36.8 s)
$ mt data tick adopt --job-id GLBX-20250123-XT4GD5UM6C --source /data/restore-test/228-drill-20261010T032157Z/restic-restore/data/tick-archive/GLBX-20250123-XT4GD5UM6C  (exit 0, 115.4 s)
$ mt data tick adopt --job-id GLBX-20260930-DLDYL5DM8Q --source /data/restore-test/228-drill-20261010T032157Z/restic-restore/data/tick-archive/GLBX-20260930-DLDYL5DM8Q  (exit 0, 38.8 s)
$ mt data tick adopt --job-id GLBX-20260930-HVGRLYKHRN --source /data/restore-test/228-drill-20261010T032157Z/restic-restore/data/tick-archive/GLBX-20260930-HVGRLYKHRN  (exit 0, 37.2 s)
$ mt data tick adopt --job-id GLBX-20260930-MBERAR6R7T --source /data/restore-test/228-drill-20261010T032157Z/restic-restore/data/tick-archive/GLBX-20260930-MBERAR6R7T  (exit 0, 2.5 s)
$ mt data tick adopt --job-id GLBX-20260930-VDPHT5ESUC --source /data/restore-test/228-drill-20261010T032157Z/restic-restore/data/tick-archive/GLBX-20260930-VDPHT5ESUC  (exit 0, 44.1 s)
$ mt data tick pass --estimate-only  (exit 0, 2.2 s)
$ mt data tick ingest  (exit 0, 77.2 s)
```

## Step 7: compare rebuild with restore — PASS

Expected: tick_trade fingerprint equal; bookkeeping differences only in TD4's set

Seen:

```
tick_trade fingerprint differences: expected [], seen []
allowed difference: tick_archive_unit.state_changed_at on 156 row(s)
allowed difference: tick_dataset_edge.available_end on 1 row(s)
allowed difference: tick_dataset_edge.observed_at on 1 row(s)
allowed difference: tick_day_condition.observed_at on 106 row(s)
allowed-missing requests: 0
bookkeeping failures: expected [], seen []
```

## Step 8: clean up — PASS

Expected: no drill directory, no scratch server

Seen:

```
scratch server stopped: True
removed /data/restore-test/228-drill-20261010T032157Z
```

## Timings

- restic restore (s): 14.7
- base extraction (s): 2.6
- recovery (s): 1.6
- compare with production (s): 91.1
- rebuild (s): 355.3

## Notes

- none

## Evidence: restic ls --json (first lines)

```
{"time":"2026-10-09T04:00:10.607298912-06:00","parent":"50ccf4e474d75644859690fa4750e944ab844eadb1af3e0b7e1b0cd35cf9e64d","tree":"b3f87e64e36535d03704fbd2939773da7f184d33d6a6eef6b636ae3797436120","paths":["/etc","/root","/var/spool/cron/crontabs","/home/manta","/data/tick-archive"],"hostname":"manta9000","username":"root","program_version":"restic 0.18.1","summary":{"backup_start":"2026-10-09T04:00:10.607298912-06:00","backup_end":"2026-10-09T04:00:32.251257648-06:00","files_new":95,"files_changed":109,"files_unmodified":222243,"dirs_new":29,"dirs_changed":113,"dirs_unmodified":29242,"data_blobs":234,"tree_blobs":141,"data_added":96479705,"data_added_packed":36920803,"total_files_processed":222447,"total_bytes_processed":117948220497},"id":"4f4fc36dfb51cc502eaabccbdd810897921ee8d2db1a7a4c842b70f6d0897729","short_id":"4f4fc36d","message_type":"snapshot","struct_type":"snapshot"}
{"name":"tick-archive","type":"dir","path":"/data/tick-archive","uid":1000,"gid":1000,"mode":2147484157,"permissions":"drwxrwxr-x","mtime":"2026-09-29T21:14:03.734135282-06:00","atime":"2026-09-29T21:14:03.734135282-06:00","ctime":"2026-09-29T21:14:03.734135282-06:00","inode":102236161,"message_type":"node","struct_type":"node"}
{"name":"GLBX-20240930-USM7UXXJBA","type":"dir","path":"/data/tick-archive/GLBX-20240930-USM7UXXJBA","uid":1000,"gid":1000,"mode":2147484157,"permissions":"drwxrwxr-x","mtime":"2026-09-28T23:39:06.808041839-06:00","atime":"2026-09-28T23:39:06.808041839-06:00","ctime":"2026-09-28T23:39:06.808041839-06:00","inode":102236162,"message_type":"node","struct_type":"node"}
{"name":"condition.json","type":"file","path":"/data/tick-archive/GLBX-20240930-USM7UXXJBA/condition.json","uid":1000,"gid":1000,"size":3122,"mode":436,"permissions":"-rw-rw-r--","mtime":"2026-09-28T23:39:06.607935962-06:00","atime":"2026-09-28T23:39:06.607935962-06:00","ctime":"2026-09-28T23:39:06.607982233-06:00","inode":102236163,"message_type":"node","struct_type":"node"}
{"name":"glbx-mdp3-20240830.trades.dbn.zst","type":"file","path":"/data/tick-archive/GLBX-20240930-USM7UXXJBA/glbx-mdp3-20240830.trades.dbn.zst","uid":1000,"gid":1000,"size":5988867,"mode":436,"permissions":"-rw-rw-r--","mtime":"2026-09-28T23:39:06.615857362-06:00","atime":"2026-09-28T23:39:06.615857362-06:00","ctime":"2026-09-28T23:39:06.616857382-06:00","inode":102236166,"message_type":"node","struct_type":"node"}
{"name":"glbx-mdp3-20240901.trades.dbn.zst","type":"file","path":"/data/tick-archive/GLBX-20240930-USM7UXXJBA/glbx-mdp3-20240901.trades.dbn.zst","uid":1000,"gid":1000,"size":55297,"mode":436,"permissions":"-rw-rw-r--","mtime":"2026-09-28T23:39:06.617411697-06:00","atime":"2026-09-28T23:39:06.617411697-06:00","ctime":"2026-09-28T23:39:06.617482158-06:00","inode":102236167,"message_type":"node","struct_type":"node"}
{"name":"glbx-mdp3-20240902.trades.dbn.zst","type":"file","path":"/data/tick-archive/GLBX-20240930-USM7UXXJBA/glbx-mdp3-20240902.trades.dbn.zst","uid":1000,"gid":1000,"size":696862,"mode":436,"permissions":"-rw-rw-r--","mtime":"2026-09-28T23:39:06.617857402-06:00","atime":"2026-09-28T23:39:06.617857402-06:00","ctime":"2026-09-28T23:39:06.61852206-06:00","inode":102236168,"message_type":"node","struct_type":"node"}
{"name":"glbx-mdp3-20240903.trades.dbn.zst","type":"file","path":"/data/tick-archive/GLBX-20240930-USM7UXXJBA/glbx-mdp3-20240903.trades.dbn.zst","uid":1000,"gid":1000,"size":7575036,"mode":436,"permissions":"-rw-rw-r--","mtime":"2026-09-28T23:39:06.627857602-06:00","atime":"2026-09-28T23:39:06.627857602-06:00","ctime":"2026-09-28T23:39:06.628416353-06:00","inode":102236169,"message_type":"node","struct_type":"node"}
```

## Evidence: server log (restored lines, last)

```
2026-10-10 03:22:25.742 GMT [1810762] LOG:  restored log file "00000001000000290000007C" from archive
2026-10-10 03:22:25.764 GMT [1810762] LOG:  restored log file "00000001000000290000007D" from archive
2026-10-10 03:22:25.786 GMT [1810762] LOG:  restored log file "00000001000000290000007E" from archive
2026-10-10 03:22:25.809 GMT [1810762] LOG:  restored log file "00000001000000290000007F" from archive
2026-10-10 03:22:25.832 GMT [1810762] LOG:  restored log file "000000010000002900000080" from archive
2026-10-10 03:22:25.856 GMT [1810762] LOG:  restored log file "000000010000002900000081" from archive
2026-10-10 03:22:25.878 GMT [1810762] LOG:  restored log file "000000010000002900000082" from archive
2026-10-10 03:22:25.906 GMT [1810762] LOG:  restored log file "000000010000002900000082" from archive
```

PASS: archive, database, fallback
