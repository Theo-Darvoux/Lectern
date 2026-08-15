-- storage_resolve_cas_mutation_v2
local mutation_id = ARGV[1]
local expected_epoch = tonumber(ARGV[2])
local physical_usage = tonumber(ARGV[3])
if not mutation_id or mutation_id == '' or expected_epoch == nil
   or physical_usage == nil or physical_usage < 0 then return -2 end
local current_epoch = tonumber(redis.call('GET', KEYS[4]) or '0')
if current_epoch ~= expected_epoch then return 0 end
local raw_intent = redis.call('HGET', KEYS[5], mutation_id)
if not raw_intent then return -1 end
local ok, intent = pcall(cjson.decode, raw_intent)
if not ok or type(intent) ~= 'table' then return -2 end
if tonumber(intent.epoch) ~= expected_epoch or tonumber(intent.journal_version) ~= 3
   or intent.phase ~= 'dispatched' then return 0 end
local generation = tonumber(redis.call('GET', KEYS[2]) or '0')
if generation == nil or generation < 0 then return -2 end
redis.call('SET', KEYS[1], physical_usage)
redis.call('SET', KEYS[2], generation + 1)
redis.call('HDEL', KEYS[5], mutation_id)
redis.call('INCR', KEYS[4])
if redis.call('HLEN', KEYS[5]) == 0 then redis.call('DEL', KEYS[3]) end
return physical_usage
