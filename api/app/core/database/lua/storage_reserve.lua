local expiries = KEYS[1]
local sizes = KEYS[2]
local total_key = KEYS[3]
local usage_key = KEYS[4]
local legacy_usage_key = KEYS[5]
local generation_key = KEYS[6]

local reservation_id = ARGV[1]
local requested_size = tonumber(ARGV[2])
local expires_at = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local capacity = tonumber(ARGV[5])
local expected_generation = tonumber(ARGV[6]) or 0
local legacy_snapshot = tonumber(ARGV[7])

if not requested_size or not expires_at or not now or not capacity or not legacy_snapshot then
    return -3
end

local current_generation = tonumber(redis.call('GET', generation_key)) or 0
if current_generation ~= expected_generation then
    return -2
end

-- Install the DB snapshot only after its generation has been validated, in the
-- same atomic script as the capacity decision. A promoted legacy handoff bumps
-- the generation before releasing its staging reservation.
redis.call('SET', legacy_usage_key, legacy_snapshot)

local total = tonumber(redis.call('GET', total_key)) or 0
local expired = redis.call('ZRANGEBYSCORE', expiries, '-inf', now)
for _, expired_id in ipairs(expired) do
    local expired_size = tonumber(redis.call('HGET', sizes, expired_id)) or 0
    total = math.max(0, total - expired_size)
    redis.call('HDEL', sizes, expired_id)
end
if #expired > 0 then
    redis.call('ZREMRANGEBYSCORE', expiries, '-inf', now)
end

local previous_size = tonumber(redis.call('HGET', sizes, reservation_id)) or 0
local next_total = total - previous_size + requested_size
local cas_usage = tonumber(redis.call('GET', usage_key)) or 0
if cas_usage + legacy_snapshot + next_total > capacity then
    redis.call('SET', total_key, total)
    return 0
end

redis.call('HSET', sizes, reservation_id, requested_size)
redis.call('ZADD', expiries, expires_at, reservation_id)
redis.call('SET', total_key, next_total)
return 1
