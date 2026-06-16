import { z } from 'zod'

const envSchema = z.object({
  NEXT_PUBLIC_API_URL: z.string().url().default('http://localhost:8000'),
  NEXT_PUBLIC_WS_URL: z.string().default('ws://localhost:8000/ws'),
  NEXT_PUBLIC_CF_TURNSTILE_SITE_KEY: z.string().default('1x00000000000000000000AA'),
})

const parsed = envSchema.safeParse({
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
  NEXT_PUBLIC_WS_URL: process.env.NEXT_PUBLIC_WS_URL,
  NEXT_PUBLIC_CF_TURNSTILE_SITE_KEY: process.env.NEXT_PUBLIC_CF_TURNSTILE_SITE_KEY,
})

if (!parsed.success) {
  console.error('Invalid environment variables:', parsed.error.flatten().fieldErrors)
  throw new Error('Invalid environment variables')
}

export const env = parsed.data
