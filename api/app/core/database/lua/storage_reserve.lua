local expiries = KEYS[1]
local sizes = KEYS[2]
local total_key = KEYS[3]
local usage_key = KEYS[4]

local reservation_id = ARGV[1]
local requested_size = tonumber(ARGV[2])
local expires_at = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local capacity = tonumber(ARGV[5])

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
-- Read physical usage in the same script as the reservation update. Passing a
-- value read by the caller would allow a concurrent CAS finalize to make the
-- capacity decision against stale usage.
local usage = tonumber(redis.call('GET', usage_key)) or 0
if usage + next_total > capacity then
    redis.call('SET', total_key, total)
    return 0
end

redis.call('HSET', sizes, reservation_id, requested_size)
redis.call('ZADD', expiries, expires_at, reservation_id)
redis.call('SET', total_key, next_total)
return 1
