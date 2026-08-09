-- storage_begin_cas_mutation_v2
local mutation_id = ARGV[1]
local operation = ARGV[2]
local file_key = ARGV[3]
local recovery_delay_ms = tonumber(ARGV[4])
if not mutation_id or mutation_id == '' or not operation or not file_key
   or recovery_delay_ms == nil or recovery_delay_ms <= 0 then
  return -2
end
if redis.call('HLEN', KEYS[3]) ~= 0 then
  return -1
end

local epoch = tonumber(redis.call('GET', KEYS[2]) or '0')
if epoch == nil or epoch < 0 then
  return -2
end

local redis_time = redis.call('TIME')
local started_at_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local recover_after_ms = started_at_ms + recovery_delay_ms
local next_epoch = epoch + 1

redis.call('SET', KEYS[2], next_epoch)
redis.call(
  'HSET',
  KEYS[3],
  mutation_id,
  cjson.encode({
    operation = operation,
    file_key = file_key,
    started_at_ms = started_at_ms,
    recover_after_ms = recover_after_ms,
    epoch = next_epoch
  })
)
redis.call('SET', KEYS[1], '1')
return next_epoch
