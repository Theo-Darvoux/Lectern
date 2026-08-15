local sem_key = KEYS[1]
local limit = tonumber(ARGV[1])
local holder_id = ARGV[2]
local op = ARGV[3] or "acquire"
local expire_ms = tonumber(ARGV[5])

if op ~= "acquire" and op ~= "renew" then
    return redis.error_reply("invalid semaphore operation")
end

local redis_time = redis.call('TIME')
local now_ms = (tonumber(redis_time[1]) * 1000) + math.floor(tonumber(redis_time[2]) / 1000)
local expires_at = now_ms + expire_ms

if op == "renew" then
    local existing_score = redis.call('ZSCORE', sem_key, holder_id)
    if not existing_score then
        return 0
    end
    if tonumber(existing_score) <= now_ms then
        return 0
    end
    redis.call('ZADD', sem_key, expires_at, holder_id)
    redis.call('PEXPIRE', sem_key, expire_ms + 60000)
    return 1
end

-- Acquire mode: remove expired holders first
redis.call('ZREMRANGEBYSCORE', sem_key, 0, now_ms)

local existing_score = redis.call('ZSCORE', sem_key, holder_id)
if existing_score then
    redis.call('ZADD', sem_key, expires_at, holder_id)
    redis.call('PEXPIRE', sem_key, expire_ms + 60000)
    return 1
end

local count = redis.call('ZCARD', sem_key)
if count < limit then
    redis.call('ZADD', sem_key, expires_at, holder_id)
    redis.call('PEXPIRE', sem_key, expire_ms + 60000)
    return 1
end
return 0
