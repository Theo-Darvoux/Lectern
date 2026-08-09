-- storage_reconcile_cas_usage_v1
local expected_generation = tonumber(ARGV[1])
local physical_usage = tonumber(ARGV[2])
if not expected_generation or not physical_usage or physical_usage < 0 then
  return -2
end
local current_generation = tonumber(redis.call('GET', KEYS[2]) or '0')
if current_generation ~= expected_generation then
  return 0
end
redis.call('SET', KEYS[1], physical_usage)
return 1
