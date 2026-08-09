-- storage_commit_cas_delta_v3
local delta = tonumber(ARGV[1])
local mutation_id = ARGV[2]
local expected_epoch = tonumber(ARGV[3])
if delta == nil or not mutation_id or mutation_id == '' or expected_epoch == nil then return -2 end
local current_epoch = tonumber(redis.call('GET', KEYS[4]) or '0')
if current_epoch ~= expected_epoch then return -4 end
local raw_intent = redis.call('HGET', KEYS[5], mutation_id)
if not raw_intent then return -4 end
local ok, intent = pcall(cjson.decode, raw_intent)
if not ok or type(intent) ~= 'table' then return -2 end
if tonumber(intent.epoch) ~= expected_epoch or tonumber(intent.journal_version) ~= 3
   or intent.phase ~= 'dispatched' then return -4 end
local raw_usage = redis.call('GET', KEYS[1])
local raw_generation = redis.call('GET', KEYS[2])
if not raw_usage or not raw_generation then return -3 end
local usage = tonumber(raw_usage)
local generation = tonumber(raw_generation)
if not usage or not generation or usage < 0 or generation < 0 then return -2 end
local updated = usage + delta
if updated < 0 then return -1 end
redis.call('SET', KEYS[1], updated)
redis.call('INCR', KEYS[2])
redis.call('HDEL', KEYS[5], mutation_id)
redis.call('INCR', KEYS[4])
if redis.call('HLEN', KEYS[5]) == 0 then redis.call('DEL', KEYS[3]) end
return updated
