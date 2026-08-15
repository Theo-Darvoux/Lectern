"use client";

import { useEffect, useRef, useState } from "react";

const VERTEX_SHADER = `
attribute vec2 aPosition;
attribute vec2 aUv;
varying vec2 vUv;
void main() {
  vUv = aUv;
  gl_Position = vec4(aPosition, 0.0, 1.0);
}
`;

const FRAGMENT_SHADER = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif

uniform sampler2D tText;
uniform float uTime;
uniform vec2 uResolution;
varying vec2 vUv;

// Ultra-smooth 4-point Starburst Flare for 90s sparkles
float calcStar(vec2 p, float intensity) {
  float d = length(p);
  if (d > 0.18 || intensity <= 0.01) return 0.0;
  
  float rayH = intensity * 0.003 / (abs(p.y) * 14.0 + abs(p.x) * 1.0 + 0.008);
  float rayV = intensity * 0.003 / (abs(p.x) * 14.0 + abs(p.y) * 1.0 + 0.008);
  float rayD1 = intensity * 0.0015 / (abs(p.x + p.y) * 10.0 + abs(p.x - p.y) * 1.0 + 0.01);
  float rayD2 = intensity * 0.0015 / (abs(p.x - p.y) * 10.0 + abs(p.x + p.y) * 1.0 + 0.01);
  float core = intensity * 0.022 / (d * 8.0 + 0.025);
  
  float falloff = smoothstep(0.18, 0.03, d);
  return (rayH + rayV + rayD1 + rayD2 + core) * falloff;
}

void main() {
  vec2 uv = vUv;
  vec4 sampleCenter = texture2D(tText, uv);
  
  float alpha = sampleCenter.a;
  if (alpha < 0.005) {
    discard;
  }

  // Smooth multi-channel decoding
  // R: Heightfield (smooth bevel slope)
  // G: Front Face Mask (1.0 = face, 0.0 = extrusion side)
  // B: Depth Progress (0.0 = back, 1.0 = front)
  float isFace = smoothstep(0.05, 0.65, sampleCenter.g);
  float depthProgress = sampleCenter.b;

  // Multi-tap smooth normal derivation from R (Height) channel
  vec2 texel = vec2(1.5 / max(uResolution.x, 300.0), 1.5 / max(uResolution.y, 100.0));
  
  float hL = texture2D(tText, uv - vec2(texel.x * 2.0, 0.0)).r;
  float hR = texture2D(tText, uv + vec2(texel.x * 2.0, 0.0)).r;
  float hD = texture2D(tText, uv - vec2(0.0, texel.y * 2.0)).r;
  float hU = texture2D(tText, uv + vec2(0.0, texel.y * 2.0)).r;
  
  vec3 N = normalize(vec3(-(hR - hL) * 5.0, -(hU - hD) * 5.0, 1.0));
  vec3 V = vec3(0.0, 0.0, 1.0);
  vec3 R = reflect(-V, N);

  // --- 90s CHROME / RAYTRACED HORIZON ENVIRONMENT MAPPING ---
  float wave = sin(uv.x * 7.0 + uTime * 0.55) * 0.035;
  float refY = R.y + R.x * 0.32 + wave;

  // 90s Sky / Ground Chrome Palette
  vec3 skyZenith   = vec3(0.38, 0.58, 0.92); // Cobalt steel blue
  vec3 skyMid      = vec3(0.82, 0.90, 1.00); // Polished silver sky
  vec3 solarFlare  = vec3(1.15, 1.15, 1.20); // Radiant white horizon band
  vec3 horizonDark = vec3(0.08, 0.08, 0.12); // Iconic 90s dark horizon line
  vec3 groundWarm  = vec3(0.60, 0.46, 0.32); // Warm bronze metallic earth
  vec3 groundDeep  = vec3(0.22, 0.24, 0.32); // Deep gunmetal graphite

  vec3 chromeSky = mix(solarFlare, skyZenith, smoothstep(0.0, 0.85, max(refY, 0.0)));
  chromeSky = mix(chromeSky, skyMid, smoothstep(0.10, 0.45, max(refY, 0.0)));

  vec3 chromeGround = mix(horizonDark, groundWarm, smoothstep(0.0, -0.38, min(refY, 0.0)));
  chromeGround = mix(chromeGround, groundDeep, smoothstep(-0.38, -0.80, min(refY, 0.0)));

  vec3 chromeColor = refY >= 0.0 ? chromeSky : chromeGround;
  
  // Horizon glare line
  float horizonGlint = exp(-pow(refY, 2.0) * 140.0) * 0.75;
  chromeColor += vec3(1.0, 1.0, 1.0) * horizonGlint;

  // --- 90s SPECULAR LIGHTING & BEVEL CHISEL HIGHLIGHTS ---
  vec3 light1Pos = normalize(vec3(sin(uTime * 0.65) * 0.6 + 0.2, 0.75, 0.75));
  vec3 H1 = normalize(light1Pos + V);
  float spec1 = pow(max(dot(N, H1), 0.0), 30.0) * 0.85;

  vec3 light2Pos = normalize(vec3(-0.5, 0.85, 0.5));
  vec3 H2 = normalize(light2Pos + V);
  float spec2 = pow(max(dot(N, H2), 0.0), 18.0) * 0.35;

  float rim = pow(1.0 - abs(N.z), 2.2) * 0.55;

  // Holographic CD-ROM rainbow diffraction sheen on bevels
  vec3 rainbow = 0.5 + 0.5 * cos(vec3(0.0, 2.0, 4.0) + (N.x * 2.0 + N.y * 3.0 + uTime * 0.3) * 3.1415);
  float rainbowFactor = rim * 0.22;

  vec3 faceColor = chromeColor + (spec1 + spec2) * vec3(1.0, 0.98, 0.95) + rim * vec3(0.95, 0.98, 1.0) * 0.5;
  faceColor += rainbow * rainbowFactor;

  // --- 3D EXTRUDED SIDE WALLS ---
  vec3 sideGrad = mix(vec3(0.20, 0.22, 0.30), vec3(0.62, 0.65, 0.76), depthProgress);
  float sideLight = max(dot(N, normalize(vec3(-0.6, 0.75, 0.5))), 0.0) * 0.45 + 0.55;
  vec3 sideColor = sideGrad * sideLight;
  sideColor *= 1.0 + sin(depthProgress * 40.0) * 0.05; // Smooth machining groove hint

  vec3 finalColor = mix(sideColor, faceColor, isFace);

  // --- 90s LASER LIGHT SWEEP ---
  float sweepCycle = fract(uTime * 0.24);
  float sweepX = sweepCycle * 2.6 - 0.8;
  float sweepLine = (uv.x * 1.15 + uv.y * 0.35) - sweepX;
  float sweepGlow = exp(-sweepLine * sweepLine * 70.0) * 0.75 * isFace;
  finalColor += vec3(0.95, 0.98, 1.0) * sweepGlow;

  // --- 90s 4-POINT STARBURST SPARKLES ---
  float star1Phase = fract(uTime * 0.15);
  vec2 star1Pos = vec2(0.20 + star1Phase * 0.60, 0.68 + sin(star1Phase * 6.283) * 0.04);
  float star1Twinkle = pow(sin(uTime * 5.0) * 0.5 + 0.5, 2.0);
  float star1 = calcStar(uv - star1Pos, star1Twinkle * 0.75);

  vec2 star2Pos = vec2(0.80, 0.69);
  float star2Twinkle = pow(sin(uTime * 3.8 + 2.0) * 0.5 + 0.5, 3.0);
  float star2 = calcStar(uv - star2Pos, star2Twinkle * 0.65);

  vec2 star3Pos = vec2(0.26, 0.60);
  float star3Twinkle = pow(sin(uTime * 2.8 + 4.0) * 0.5 + 0.5, 4.0);
  float star3 = calcStar(uv - star3Pos, star3Twinkle * 0.7);

  finalColor += (star1 + star2 + star3) * vec3(1.0, 1.0, 1.0);

  // Tone curve for crisp contrast
  finalColor = pow(finalColor, vec3(0.95));

  gl_FragColor = vec4(finalColor, alpha);
}
`;

interface ShaderTextProps {
    text: string;
    className?: string;
}

export function ShaderText({ text, className }: ShaderTextProps) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const [webglSupported, setWebglSupported] = useState(true);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        let gl: WebGLRenderingContext | null = null;
        try {
            gl = canvas.getContext("webgl", { alpha: true, antialias: true, preserveDrawingBuffer: true });
        } catch {
            gl = null;
        }

        if (!gl) {
            setWebglSupported(false);
            return;
        }

        const createShader = (type: number, source: string) => {
            const shader = gl!.createShader(type);
            if (!shader) return null;
            gl!.shaderSource(shader, source);
            gl!.compileShader(shader);
            if (!gl!.getShaderParameter(shader, gl!.COMPILE_STATUS)) {
                console.error("Shader error:", gl!.getShaderInfoLog(shader));
                gl!.deleteShader(shader);
                return null;
            }
            return shader;
        };

        const vertShader = createShader(gl.VERTEX_SHADER, VERTEX_SHADER);
        const fragShader = createShader(gl.FRAGMENT_SHADER, FRAGMENT_SHADER);

        if (!vertShader || !fragShader) {
            setWebglSupported(false);
            return;
        }

        const program = gl.createProgram();
        if (!program) {
            setWebglSupported(false);
            return;
        }

        gl.attachShader(program, vertShader);
        gl.attachShader(program, fragShader);
        gl.linkProgram(program);

        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
            console.error("Program link error:", gl.getProgramInfoLog(program));
            setWebglSupported(false);
            return;
        }

        gl.useProgram(program);

        // High-definition 2D canvas generation for smooth 3D extruded title
        const scale = 2;
        const textCanvas = document.createElement("canvas");
        const texW = 1400 * scale;
        const texH = 420 * scale;
        textCanvas.width = texW;
        textCanvas.height = texH;

        const ctx = textCanvas.getContext("2d");
        if (!ctx) {
            setWebglSupported(false);
            return;
        }

        const drawText = () => {
            ctx.clearRect(0, 0, texW, texH);

            const fontName = "'Cinzel', 'Arial Black', 'Impact', 'Trebuchet MS', system-ui, sans-serif";
            const centerX = texW * 0.5;
            const centerY = texH * 0.52;

            ctx.textAlign = "center";
            ctx.textBaseline = "middle";

            // Auto-fit font size to fill ~88% of texture width for maximal presence inside card
            let fontSize = 210 * scale;
            ctx.font = `italic 900 ${fontSize}px ${fontName}`;
            const metrics = ctx.measureText(text);
            const targetWidth = texW * 0.88;
            if (metrics.width > 0) {
                fontSize = Math.min(fontSize * (targetWidth / metrics.width), 230 * scale);
            }
            const fontStr = `italic 900 ${fontSize}px ${fontName}`;
            ctx.font = fontStr;

            const depthX = 14 * scale;
            const depthY = 16 * scale;
            // High-density subpixel stepping to eliminate any stepping gaps
            const steps = Math.ceil(Math.hypot(depthX, depthY) * 2);

            // 1. Soft Drop Shadow
            ctx.save();
            ctx.fillStyle = "rgba(0, 0, 0, 0.65)";
            ctx.shadowColor = "rgba(0, 0, 0, 0.9)";
            ctx.shadowBlur = 14 * scale;
            ctx.shadowOffsetX = depthX + 4 * scale;
            ctx.shadowOffsetY = depthY + 6 * scale;
            ctx.fillText(text, centerX, centerY);
            ctx.restore();

            // 2. Continuous 3D Extrusion (Back to Front)
            // Encoded channels: R = Height (moderate), G = 0 (side wall), B = depth progress (0 -> 220)
            for (let i = steps; i >= 1; i--) {
                const progress = 1.0 - (i / steps);
                const offX = (1.0 - progress) * depthX;
                const offY = (1.0 - progress) * depthY;
                const bVal = Math.round(50 + progress * 160);
                const rVal = Math.round(70 + progress * 40);
                ctx.fillStyle = `rgb(${rVal}, 0, ${bVal})`;
                ctx.shadowColor = "transparent";
                ctx.shadowBlur = 0;
                ctx.fillText(text, centerX + offX, centerY + offY);
            }

            // 3. Bevel Edge Chamfer Stroke: R = 160 (Bevel slope), G = 0, B = 240
            ctx.save();
            ctx.lineWidth = 6 * scale;
            ctx.strokeStyle = "rgb(160, 0, 240)";
            ctx.strokeText(text, centerX, centerY);
            ctx.restore();

            // 4. Pure White Front Face: R = 255 (Max height), G = 255 (Full face mask), B = 255 (Top depth)
            ctx.fillStyle = "rgb(255, 255, 255)";
            ctx.fillText(text, centerX, centerY);
        };

        drawText();

        // Texture Upload
        const texture = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, texture);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, textCanvas);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);

        const updateTexture = () => {
            if (!gl || !texture) return;
            drawText();
            gl.bindTexture(gl.TEXTURE_2D, texture);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, textCanvas);
        };

        if (document.fonts) {
            document.fonts.ready.then(() => updateTexture());
        }

        // Setup Quad Geometry
        const posBuffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, posBuffer);
        gl.bufferData(
            gl.ARRAY_BUFFER,
            new Float32Array([
                -1, -1,
                 1, -1,
                -1,  1,
                -1,  1,
                 1, -1,
                 1,  1,
            ]),
            gl.STATIC_DRAW
        );

        const aPosition = gl.getAttribLocation(program, "aPosition");
        gl.enableVertexAttribArray(aPosition);
        gl.vertexAttribPointer(aPosition, 2, gl.FLOAT, false, 0, 0);

        const uvBuffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, uvBuffer);
        gl.bufferData(
            gl.ARRAY_BUFFER,
            new Float32Array([
                0, 0,
                1, 0,
                0, 1,
                0, 1,
                1, 0,
                1, 1,
            ]),
            gl.STATIC_DRAW
        );

        const aUv = gl.getAttribLocation(program, "aUv");
        gl.enableVertexAttribArray(aUv);
        gl.vertexAttribPointer(aUv, 2, gl.FLOAT, false, 0, 0);

        const uTimeLoc = gl.getUniformLocation(program, "uTime");
        const uResolutionLoc = gl.getUniformLocation(program, "uResolution");
        const tTextLoc = gl.getUniformLocation(program, "tText");

        gl.uniform1i(tTextLoc, 0);

        const updateCanvasSize = () => {
            if (!canvas || !gl) return;
            const dpr = Math.min(window.devicePixelRatio || 1, 2);
            const rect = canvas.getBoundingClientRect();
            const displayW = Math.max(Math.round(rect.width * dpr), 200);
            const displayH = Math.max(Math.round(rect.height * dpr), 50);

            if (canvas.width !== displayW || canvas.height !== displayH) {
                canvas.width = displayW;
                canvas.height = displayH;
                gl.viewport(0, 0, displayW, displayH);
            }
        };

        updateCanvasSize();
        const resizeObserver = new ResizeObserver(() => updateCanvasSize());
        resizeObserver.observe(canvas);

        let animationFrameId: number;
        const startTime = performance.now();

        const render = () => {
            if (!gl) return;
            const elapsed = (performance.now() - startTime) * 0.001;
            gl.uniform1f(uTimeLoc, elapsed);
            if (uResolutionLoc && canvas) {
                gl.uniform2f(uResolutionLoc, canvas.width, canvas.height);
            }

            gl.clearColor(0, 0, 0, 0);
            gl.clear(gl.COLOR_BUFFER_BIT);
            gl.drawArrays(gl.TRIANGLES, 0, 6);

            animationFrameId = requestAnimationFrame(render);
        };

        render();

        return () => {
            cancelAnimationFrame(animationFrameId);
            resizeObserver.disconnect();
            if (gl) {
                gl.deleteTexture(texture);
                gl.deleteBuffer(posBuffer);
                gl.deleteBuffer(uvBuffer);
                gl.deleteProgram(program);
                gl.deleteShader(vertShader);
                gl.deleteShader(fragShader);
            }
        };
    }, [text]);

    if (!webglSupported) {
        return (
            <span className={className}>
                {text}
            </span>
        );
    }

    return (
        <div className="relative flex items-center justify-center w-full my-0 py-0 overflow-visible">
            <canvas
                ref={canvasRef}
                className="w-full max-w-[390px] h-[130px] sm:h-[150px] object-contain block mx-auto pointer-events-none select-none"
                aria-hidden="true"
            />
            <span className="sr-only">{text}</span>
        </div>
    );
}
