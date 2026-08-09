-- storage_commit_cas_delta_v1
local delta = tonumber(ARGV[1])
if delta == nil then
  return -2
end
local raw_usage = redis.call('GET', KEYS[1])
local raw_generation = redis.call('GET', KEYS[2])
if not raw_usage or not raw_generation then
  return -3
end
local usage = tonumber(raw_usage)
local generation = tonumber(raw_generation)
if not usage or not generation or usage < 0 or generation < 0 then
  return -2
end
local updated = usage + delta
if updated < 0 then
  return -1
end
redis.call('SET', KEYS[1], updated)
redis.call('INCR', KEYS[2])
redis.call('DEL', KEYS[3])
return updated
