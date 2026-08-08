"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { 
    Play, 
    Pause, 
    Volume2, 
    VolumeX, 
    RotateCcw, 
    RotateCw,
    Gauge,
    Music
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { 
    DropdownMenu, 
    DropdownMenuContent, 
    DropdownMenuItem, 
    DropdownMenuTrigger 
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { useTheme } from "next-themes";
import { API_BASE, fetchMaterialBlob } from "@/lib/api-client";
import { ViewerShell } from "./viewer-shell";
import { useTranslations } from "next-intl";

interface AudioPlayerProps {
    fileKey: string;
    materialId: string;
}

export function AudioPlayer({ materialId, fileKey }: AudioPlayerProps) {
    const t = useTranslations("Viewers");
    const { resolvedTheme } = useTheme();
    const [isLoading, setIsLoading] = useState(true);
    const [isPlaying, setIsPlaying] = useState(false);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(0);
    const [volume, setVolume] = useState(0.8);
    const [isMuted, setIsMuted] = useState(false);
    const [playbackRate, setPlaybackRate] = useState(1);
    const [audioBuffer, setAudioBuffer] = useState<AudioBuffer | null>(null);
 
    const streamUrl = `${API_BASE}/materials/${materialId}/file?v=${fileKey}`;
 
    const audioRef = useRef<HTMLAudioElement>(null);
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const progressCanvasRef = useRef<HTMLCanvasElement>(null);
 
    const isDark = resolvedTheme === "dark";
 
    useEffect(() => {
        let cancelled = false;
 
        const loadAudio = async () => {
            try {
                setIsLoading(true);
                const blob = await fetchMaterialBlob(materialId);
                if (cancelled) return;
 
                const arrayBuffer = await blob.arrayBuffer();
                if (cancelled) return;
 
                try {
                    const offlineCtx = new OfflineAudioContext(1, 1, 44100);
                    const decodedData = await offlineCtx.decodeAudioData(arrayBuffer);
                    if (cancelled) return;
 
                    setAudioBuffer(decodedData);
                    setDuration(decodedData.duration);
                } catch (decodeErr) {
                    console.error("Waveform decode failed:", decodeErr);
                }
                
                setIsLoading(false);
            } catch (error) {
                console.error("Failed to load audio waveform:", error);
                if (!cancelled) setIsLoading(false);
            }
        };
 
        loadAudio();
 
        return () => { cancelled = true; };
    }, [materialId, fileKey]);

    const drawWaveform = useCallback((buffer: AudioBuffer, color: string, canvasEl: HTMLCanvasElement) => {
        const ctx = canvasEl.getContext("2d");
        if (!ctx) return;

        const dpr = window.devicePixelRatio || 1;
        const width = canvasEl.offsetWidth;
        const height = canvasEl.offsetHeight;

        canvasEl.width = width * dpr;
        canvasEl.height = height * dpr;
        ctx.scale(dpr, dpr);

        const data = buffer.getChannelData(0);
        const barWidth = 3;
        const gap = 2;
        const totalBarWidth = barWidth + gap;
        const barCount = Math.floor(width / totalBarWidth);
        const step = Math.floor(data.length / barCount);

        ctx.clearRect(0, 0, width, height);
        ctx.fillStyle = color;

        for (let i = 0; i < barCount; i++) {
            let max = 0;
            for (let j = 0; j < step; j++) {
                const datum = Math.abs(data[i * step + j]);
                if (datum > max) max = datum;
            }
            
            const x = i * totalBarWidth;
            const barHeight = Math.max(4, max * height * 0.9);
            const y = (height - barHeight) / 2;
            
            ctx.beginPath();
            if (ctx.roundRect) {
                ctx.roundRect(x, y, barWidth, barHeight, barWidth / 2);
            } else {
                ctx.rect(x, y, barWidth, barHeight);
            }
            ctx.fill();
        }
    }, []);

    useEffect(() => {
        if (!audioBuffer || !canvasRef.current) return;
        const bgColor = isDark ? "rgba(148, 163, 184, 0.2)" : "rgba(100, 116, 139, 0.35)";
        drawWaveform(audioBuffer, bgColor, canvasRef.current);
    }, [audioBuffer, drawWaveform, isDark]);

    // Progress (foreground) waveform: identical bars in the accent colour, drawn
    // once. Playback progress is revealed purely by the clip-path overlay below,
    // so the canvas pixels never change — there's no need to re-rasterise the
    // whole PCM buffer on every `timeupdate` tick.
    useEffect(() => {
        if (!audioBuffer || !progressCanvasRef.current) return;
        drawWaveform(audioBuffer, "#3b82f6", progressCanvasRef.current);
    }, [audioBuffer, drawWaveform]);

    const togglePlay = () => {
        if (!audioRef.current) return;
        if (isPlaying) {
            audioRef.current.pause();
        } else {
            audioRef.current.play();
        }
        setIsPlaying(!isPlaying);
    };

    const handleTimeUpdate = () => {
        if (audioRef.current) setCurrentTime(audioRef.current.currentTime);
    };

    const handleLoadedMetadata = () => {
        if (audioRef.current) setDuration(audioRef.current.duration);
    };

    const handleSeek = (time: number) => {
        if (audioRef.current) {
            audioRef.current.currentTime = time;
            setCurrentTime(time);
        }
    };

    const formatTime = (time: number) => {
        if (isNaN(time)) return "0:00";
        const mins = Math.floor(time / 60);
        const secs = Math.floor(time % 60);
        return `${mins}:${secs.toString().padStart(2, "0")}`;
    };

    const skip = (amount: number) => {
        if (audioRef.current) {
            audioRef.current.currentTime = Math.max(0, Math.min(duration, audioRef.current.currentTime + amount));
        }
    };

    const changePlaybackRate = (rate: number) => {
        setPlaybackRate(rate);
        if (audioRef.current) audioRef.current.playbackRate = rate;
    };

    const toggleMute = () => {
        setIsMuted(!isMuted);
        if (audioRef.current) audioRef.current.muted = !isMuted;
    };

    const handleVolumeChange = (v: number) => {
        setVolume(v);
        setIsMuted(v === 0);
        if (audioRef.current) {
            audioRef.current.volume = v;
            audioRef.current.muted = v === 0;
        }
    };

    useEffect(() => {
        if (!isLoading && audioRef.current) {
            audioRef.current.playbackRate = playbackRate;
            audioRef.current.volume = volume;
            audioRef.current.muted = isMuted;
        }
    }, [isLoading, playbackRate, volume, isMuted]);

    return (
        <ViewerShell loading={false} error={null}>
            <div className="flex-1 flex flex-col items-center justify-center p-4 md:p-8 w-full h-full">
                <div className="w-full max-w-4xl mx-auto flex flex-col items-center justify-center">
            {isLoading ? (
                <div className="flex flex-col items-center gap-6">
                    <div className="relative flex items-center justify-center">
                         <div className="absolute h-24 w-24 blur-3xl bg-primary/20 rounded-full animate-pulse" />
                         <div className="h-16 w-16 rounded-2xl bg-primary/10 flex items-center justify-center border border-primary/20">
                            <Music className="h-8 w-8 text-primary animate-bounce" />
                         </div>
                    </div>
                    <div className="text-center space-y-1">
                        <p className="text-sm font-semibold">{t("preparingAudio")}</p>
                        <p className="text-xs text-muted-foreground animate-pulse">{t("analyzingFrequencies")}</p>
                    </div>
                </div>
            ) : (
                <div className="w-full relative overflow-hidden bg-card text-card-foreground rounded-[2.5rem] border shadow-2xl transition-colors duration-300 border-border/50 dark:border-white/5">
                    <div className="absolute top-0 left-1/2 -translate-x-1/2 w-1/2 h-1/2 bg-primary/5 blur-[120px] pointer-events-none" />

                    <div className="relative p-6 md:p-10 space-y-10">
                        <div className="relative group cursor-pointer select-none">
                            <div className="flex items-center justify-between mb-4 px-1">
                                <div className="flex items-center gap-3">
                                    <div className="h-2 w-2 rounded-full bg-primary animate-pulse" />
                                    <span className="text-[10px] uppercase tracking-[0.2em] font-bold text-muted-foreground/80">{t("waveformAnalysis")}</span>
                                </div>
                                <div className="flex items-center gap-4 text-xs font-mono font-medium text-muted-foreground/80 tabular-nums">
                                    <span>{formatTime(currentTime)}</span>
                                    <div className="h-3 w-px bg-border dark:bg-white/10" />
                                    <span>{formatTime(duration)}</span>
                                </div>
                            </div>

                            <div className="relative h-24 md:h-32 transition-transform duration-300 group-hover:scale-[1.01] rounded-xl overflow-hidden">
                                {audioBuffer ? (
                                    <>
                                        <canvas ref={canvasRef} className="w-full h-full block" />
                                        <div 
                                            className="absolute inset-0 pointer-events-none overflow-hidden transition-[clip-path] duration-150 ease-out"
                                            style={{ clipPath: `inset(0 ${100 - (currentTime / duration) * 100}% 0 0)` }}
                                        >
                                            <canvas className="w-full h-full block" ref={progressCanvasRef} />
                                        </div>
                                    </>
                                ) : (
                                    <div className="w-full h-full flex flex-col items-center justify-center bg-zinc-200/50 dark:bg-black/20 relative">
                                        <div 
                                            className="absolute left-0 top-0 bottom-0 bg-primary/20 transition-all duration-150 ease-out pointer-events-none"
                                            style={{ width: duration ? `${(currentTime / duration) * 100}%` : "0%" }}
                                        />
                                        <Music className="h-8 w-8 text-muted-foreground/30 z-10" />
                                    </div>
                                )}
                                <div className="absolute inset-y-0 w-px bg-foreground/20 dark:bg-white/20 opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity" id="hover-line" />
                                <div 
                                    className="absolute inset-0 z-10"
                                    onClick={(e) => {
                                        const rect = e.currentTarget.getBoundingClientRect();
                                        const x = e.clientX - rect.left;
                                        handleSeek((x / rect.width) * duration);
                                    }}
                                    onMouseMove={(e) => {
                                        const line = document.getElementById('hover-line');
                                        if (line) {
                                            const rect = e.currentTarget.getBoundingClientRect();
                                            const x = e.clientX - rect.left;
                                            line.style.left = `${x}px`;
                                        }
                                    }}
                                />
                            </div>
                        </div>

                        <div className="flex flex-col sm:flex-row items-center justify-between gap-8 pt-4 border-t border-border/50 dark:border-white/5">
                            <div className="flex items-center gap-4">
                                <DropdownMenu>
                                    <DropdownMenuTrigger asChild>
                                        <Button variant="ghost" size="sm" className="h-10 px-4 rounded-xl bg-muted/80 dark:bg-muted/50 hover:bg-muted transition-all active:scale-95 group">
                                            <Gauge className="h-4 w-4 mr-2 text-muted-foreground group-hover:text-primary transition-colors" />
                                            <span className="text-xs font-bold tabular-nums">{playbackRate}x</span>
                                        </Button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent align="center" className="rounded-2xl shadow-2xl p-1 border-border/50 dark:border-white/10">
                                        {[0.5, 0.75, 1, 1.25, 1.5, 2].map((rate) => (
                                            <DropdownMenuItem 
                                                key={rate} 
                                                onClick={() => changePlaybackRate(rate)}
                                                className={cn(
                                                    "text-xs font-semibold rounded-xl px-4 py-2 cursor-pointer transition-colors",
                                                    playbackRate === rate ? "bg-primary text-primary-foreground" : ""
                                                )}
                                            >
                                                {rate}x
                                            </DropdownMenuItem>
                                        ))}
                                    </DropdownMenuContent>
                                </DropdownMenu>
                            </div>

                            <div className="flex items-center gap-8">
                                <Button variant="ghost" size="icon" className="h-12 w-12 text-muted-foreground hover:text-foreground hover:bg-muted rounded-full transition-all active:scale-90" onClick={() => skip(-10)}>
                                    <RotateCcw className="h-6 w-6" />
                                </Button>
                                <Button size="icon" className="h-20 w-20 rounded-full shadow-lg bg-foreground hover:bg-foreground text-background hover:scale-105 active:scale-95 transition-all duration-300 group relative border-4 border-background dark:border-transparent" onClick={togglePlay}>
                                    <div className="absolute inset-0 rounded-full bg-primary opacity-0 group-hover:opacity-10 transition-opacity" />
                                    {isPlaying ? <Pause className="h-8 w-8 fill-current" /> : <Play className="h-8 w-8 fill-current ml-1" />}
                                </Button>
                                <Button variant="ghost" size="icon" className="h-12 w-12 text-muted-foreground hover:text-foreground hover:bg-muted rounded-full transition-all active:scale-90" onClick={() => skip(10)}>
                                    <RotateCw className="h-6 w-6" />
                                </Button>
                            </div>

                            <div className="flex items-center gap-4 min-w-[140px] justify-end group/volume">
                                <Button variant="ghost" size="icon" className="h-10 w-10 text-muted-foreground hover:text-foreground rounded-full transition-colors" onClick={toggleMute}>
                                    {isMuted || volume === 0 ? <VolumeX className="h-5 w-5" /> : <Volume2 className="h-5 w-5" />}
                                </Button>
                                <div className="relative w-24 h-1.5 flex items-center">
                                    <input
                                        type="range" min={0} max={1} step={0.01}
                                        value={isMuted ? 0 : volume}
                                        onChange={(e) => handleVolumeChange(parseFloat(e.target.value))}
                                        className="w-full h-full bg-muted-foreground/20 dark:bg-muted rounded-full appearance-none cursor-pointer accent-foreground transition-all group-hover/volume:h-2"
                                    />
                                </div>
                            </div>
                        </div>
                    </div>
                    <audio
                        ref={audioRef} src={streamUrl}
                        onTimeUpdate={handleTimeUpdate} onLoadedMetadata={handleLoadedMetadata}
                        onEnded={() => setIsPlaying(false)} className="hidden"
                    >
                        {t("audioNotSupported")}
                    </audio>
                </div>
            )}
                </div>
            </div>
        </ViewerShell>
    );
}
