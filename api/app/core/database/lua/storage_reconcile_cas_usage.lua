-- storage_reconcile_cas_usage_v3
local expected_generation = tonumber(ARGV[1])
local expected_mutation_epoch = tonumber(ARGV[2])
local physical_usage = tonumber(ARGV[3])
if expected_generation == nil or expected_mutation_epoch == nil or physical_usage == nil or physical_usage < 0 then return -2 end
if redis.call('HLEN', KEYS[5]) ~= 0 then return -3 end
local current_generation = tonumber(redis.call('GET', KEYS[2]) or '0')
local current_mutation_epoch = tonumber(redis.call('GET', KEYS[4]) or '0')
if current_generation ~= expected_generation or current_mutation_epoch ~= expected_mutation_epoch then return 0 end
redis.call('SET', KEYS[1], physical_usage)
redis.call('SET', KEYS[2], expected_generation)
redis.call('DEL', KEYS[3])
return 1
