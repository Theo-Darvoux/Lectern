-- storage_abort_cas_mutation_v2
local mutation_id = ARGV[1]
local expected_epoch = tonumber(ARGV[2])
local expected_phase = ARGV[3]
if not mutation_id or mutation_id == '' or expected_epoch == nil
   or not expected_phase or expected_phase == '' then return -2 end
local current_epoch = tonumber(redis.call('GET', KEYS[2]) or '0')
if current_epoch ~= expected_epoch then return 0 end
local raw_intent = redis.call('HGET', KEYS[3], mutation_id)
if not raw_intent then return -1 end
local ok, intent = pcall(cjson.decode, raw_intent)
if not ok or type(intent) ~= 'table' or tonumber(intent.epoch) ~= expected_epoch then return -2 end
if intent.phase ~= expected_phase then return -3 end
redis.call('HDEL', KEYS[3], mutation_id)
redis.call('INCR', KEYS[2])
if redis.call('HLEN', KEYS[3]) == 0 then redis.call('DEL', KEYS[1]) end
return 1
