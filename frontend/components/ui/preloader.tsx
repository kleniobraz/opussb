"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";

// Saudações multilíngues (PT primeiro) e o beat final na marca: "SB Clips" em dourado.
const words = ["Olá", "Hello", "Bonjour", "Ciao", "やあ", "Hallå", "Guten tag", "হ্যালো", "SB Clips"];

// Identidade SB: dourado da marca + preto neutro (sem o azul do #070b13 original).
const SB_GOLD = "#f5c518";
const SB_DARK = "#0a0a0b";
const SB_TEXT = "#f3f1ea";

// Cubic-bezier tipado como tupla de 4 (BezierDefinition) — array solto vira number[]
// e o TS do framer-motion recusa ("Type 'number' is not assignable to type 'Easing'").
const EASE: [number, number, number, number] = [0.76, 0, 0.24, 1];

const opacity = {
  initial: { opacity: 0 },
  enter: { opacity: 0.75, transition: { duration: 1, delay: 0.2 } },
};

const slideUp = {
  initial: { top: 0 },
  exit: {
    top: "-100vh",
    transition: { duration: 0.8, ease: EASE, delay: 0.2 },
  },
};

interface PreloaderProps {
  onComplete?: () => void;
}

export default function Preloader({ onComplete }: PreloaderProps) {
  const [index, setIndex] = useState(0);
  const [dimension, setDimension] = useState({ width: 0, height: 0 });
  const [isExiting, setIsExiting] = useState(false);

  useEffect(() => {
    setDimension({ width: window.innerWidth, height: window.innerHeight });
  }, []);

  useEffect(() => {
    if (index === words.length - 1) {
      const t1 = setTimeout(() => {
        setIsExiting(true);
        const t2 = setTimeout(() => onComplete?.(), 1000);
        return () => clearTimeout(t2);
      }, 1000);
      return () => clearTimeout(t1);
    }
    const t = setTimeout(() => setIndex(index + 1), index === 0 ? 1000 : 150);
    return () => clearTimeout(t);
  }, [index, onComplete]);

  const initialPath = `M0 0 L${dimension.width} 0 L${dimension.width} ${dimension.height} Q${dimension.width / 2} ${dimension.height + 300} 0 ${dimension.height} L0 0`;
  const targetPath = `M0 0 L${dimension.width} 0 L${dimension.width} ${dimension.height} Q${dimension.width / 2} ${dimension.height} 0 ${dimension.height} L0 0`;

  const curve = {
    initial: {
      d: initialPath,
      transition: { duration: 0.7, ease: EASE },
    },
    exit: {
      d: targetPath,
      transition: { duration: 0.7, ease: EASE, delay: 0.3 },
    },
  };

  const isLast = index === words.length - 1;

  return (
    <motion.div
      variants={slideUp}
      initial="initial"
      animate={isExiting ? "exit" : "initial"}
      className="fixed inset-0 z-[99999] flex h-screen w-screen items-center justify-center"
      style={{ backgroundColor: SB_DARK }}
    >
      {dimension.width > 0 && (
        <>
          <motion.p
            variants={opacity}
            initial="initial"
            animate="enter"
            className="absolute z-10 flex items-center text-4xl font-semibold md:text-5xl lg:text-6xl"
            style={{ color: isLast ? SB_GOLD : SB_TEXT }}
          >
            <span className="mr-3 block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: SB_GOLD }} />
            {words[index]}
          </motion.p>
          <svg className="absolute top-0 h-[calc(100%+300px)] w-full">
            <motion.path
              variants={curve}
              initial="initial"
              animate={isExiting ? "exit" : "initial"}
              fill={SB_DARK}
            />
          </svg>
        </>
      )}
    </motion.div>
  );
}
