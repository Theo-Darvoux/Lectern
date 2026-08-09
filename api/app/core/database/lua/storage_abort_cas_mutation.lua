-- storage_abort_cas_mutation_v1
local mutation_id = ARGV[1]
local expected_epoch = tonumber(ARGV[2])
if not mutation_id or mutation_id == '' or expected_epoch == nil then return -2 end
local current_epoch = tonumber(redis.call('GET', KEYS[2]) or '0')
if current_epoch ~= expected_epoch then return 0 end
if not redis.call('HGET', KEYS[3], mutation_id) then return -1 end
redis.call('HDEL', KEYS[3], mutation_id)
redis.call('INCR', KEYS[2])
if redis.call('HLEN', KEYS[3]) == 0 then redis.call('DEL', KEYS[1]) end
return 1
