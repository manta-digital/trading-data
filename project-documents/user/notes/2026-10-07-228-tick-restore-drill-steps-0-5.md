---
docType: notes
slice: tick-restore-drill
project: trading-data
dateCreated: 20261007
dateUpdated: 20261007
---

# Tick restore drill (slice 228)

Started 2026-10-07T09:37:34-06:00.

## Step 0: prepare — PASS

Expected: drill lock held; sudo primed; leftovers cleared; free space >= 3x footprint

Seen:

```
drill lock held: /data/restore-test/.228-drill.lock
leftovers removed: none
free space: need 3 x 964733640 bytes (archive + base 20261005), seen 1358372126720 free on /data/restore-test
drill directory: /data/restore-test/228-drill-20261007T153734Z
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
snapshot 2026-10-07T04:00:11.844789-06:00: 176 files, 523349320 bytes
```

## Step 2: restore point — PASS

Expected: the switched segment is archived within the bound

Seen:

```
segment the restore must reach: 00000001000000290000007B
archived: /data/backup/17-tick/wal/00000001000000290000007B.zst
```

## Step 3: restore archive — PASS

Expected: restored files and bytes equal the live archive's backed-up set

Seen:

```
chown -R 1000:1000 /data/restore-test/228-drill-20261007T153734Z/restic-restore/data/tick-archive exit: expected 0, seen 0
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
last restored segment: 00000001000000290000007B; needed 00000001000000290000007B
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
locks released 2026-10-07T15:39:35+00:00
```

## Step 8: clean up — PASS

Expected: no drill directory, no scratch server

Seen:

```
scratch server stopped: True
removed /data/restore-test/228-drill-20261007T153734Z
```

## Timings

- restic restore (s): 17.0
- base extraction (s): 2.6
- recovery (s): 0.5
- compare with production (s): 88.8

## Notes

- none

## Evidence: restic ls --json (first lines)

```
{"time":"2026-10-07T04:00:11.844789381-06:00","parent":"4f4346b18bf6296464aebe629c64a69e54aaa2651405310e67a045346885ef11","tree":"b7909c3fdaae244cb836087c9f194747cdc6f43e28a27c669f0cda99191fa384","paths":["/etc","/root","/var/spool/cron/crontabs","/home/manta","/data/tick-archive"],"hostname":"manta9000","username":"root","program_version":"restic 0.18.1","summary":{"backup_start":"2026-10-07T04:00:11.844789381-06:00","backup_end":"2026-10-07T04:03:05.664501311-06:00","files_new":508,"files_changed":921,"files_unmodified":220698,"dirs_new":90,"dirs_changed":669,"dirs_unmodified":28578,"data_blobs":1133,"tree_blobs":750,"data_added":951769212,"data_added_packed":836594445,"total_files_processed":222127,"total_bytes_processed":117799827561},"id":"d59b3310829971f1ecd9e4193fd6158f3fbd5fc044c9e3fc1bdc74f75cc3faf8","short_id":"d59b3310","message_type":"snapshot","struct_type":"snapshot"}
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
2026-10-07 15:38:06.160 GMT [222060] LOG:  restored log file "00000001000000290000007A" from archive
2026-10-07 15:38:06.188 GMT [222060] LOG:  restored log file "00000001000000290000007B" from archive
2026-10-07 15:38:06.215 GMT [222060] LOG:  restored log file "00000001000000290000007B" from archive
```

PASS through step 5 only (partial run)
