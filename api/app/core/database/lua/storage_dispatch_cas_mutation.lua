-- storage_dispatch_cas_mutation_v1
local mutation_id = ARGV[1]
local expected_epoch = tonumber(ARGV[2])
local recovery_delay_ms = tonumber(ARGV[3])
if not mutation_id or mutation_id == '' or expected_epoch == nil
   or recovery_delay_ms == nil or recovery_delay_ms <= 0 then return -2 end
local current_epoch = tonumber(redis.call('GET', KEYS[1]) or '0')
if current_epoch ~= expected_epoch then return 0 end
local raw_intent = redis.call('HGET', KEYS[2], mutation_id)
if not raw_intent then return -1 end
local ok, intent = pcall(cjson.decode, raw_intent)
if not ok or type(intent) ~= 'table' then return -2 end
if tonumber(intent.epoch) ~= expected_epoch or tonumber(intent.journal_version) ~= 3 then return -2 end
if intent.phase ~= 'preflight' then return -3 end
local redis_time = redis.call('TIME')
local dispatched_at_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local recover_after_ms = dispatched_at_ms + recovery_delay_ms
intent.phase = 'dispatched'
intent.dispatched_at_ms = dispatched_at_ms
intent.recover_after_ms = recover_after_ms
redis.call('HSET', KEYS[2], mutation_id, cjson.encode(intent))
return recover_after_ms
