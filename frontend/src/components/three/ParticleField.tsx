"use client";

import { useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";

function seedRandom(seed: number) {
  return () => {
    seed = (seed * 16807) % 2147483647;
    return (seed - 1) / 2147483646;
  };
}

const PARTICLE_COUNT = 500;
const rng = seedRandom(42);

const INITIAL_POSITIONS = (() => {
  const pos = new Float32Array(PARTICLE_COUNT * 3);
  for (let i = 0; i < PARTICLE_COUNT; i++) {
    pos[i * 3] = (rng() - 0.5) * 20;
    pos[i * 3 + 1] = (rng() - 0.5) * 20;
    pos[i * 3 + 2] = (rng() - 0.5) * 20;
  }
  return pos;
})();

const INITIAL_COLORS = (() => {
  const col = new Float32Array(PARTICLE_COUNT * 3);
  const palette = [
    [0.54, 0.36, 0.97],
    [0.02, 0.71, 0.83],
    [0.93, 0.28, 0.60],
    [0.23, 0.51, 0.96],
  ];
  for (let i = 0; i < PARTICLE_COUNT; i++) {
    const c = palette[Math.floor(rng() * palette.length)];
    col[i * 3] = c[0];
    col[i * 3 + 1] = c[1];
    col[i * 3 + 2] = c[2];
  }
  return col;
})();

function Particles() {
  const mesh = useRef<THREE.Points>(null);

  useFrame((_, delta) => {
    if (mesh.current) {
      mesh.current.rotation.y += delta * 0.02;
      mesh.current.rotation.x += delta * 0.01;
    }
  });

  return (
    <points ref={mesh}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[INITIAL_POSITIONS, 3]} />
        <bufferAttribute attach="attributes-color" args={[INITIAL_COLORS, 3]} />
      </bufferGeometry>
      <pointsMaterial size={0.04} vertexColors transparent opacity={0.6} sizeAttenuation />
    </points>
  );
}

export default function ParticleField() {
  return (
    <div className="fixed inset-0 -z-10">
      <Canvas camera={{ position: [0, 0, 5], fov: 60 }}>
        <Particles />
        <ambientLight intensity={0.5} />
      </Canvas>
    </div>
  );
}
