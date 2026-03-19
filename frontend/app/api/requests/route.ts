import { NextResponse } from 'next/server';
import { readFileSync } from 'fs';
import { join } from 'path';

export async function GET() {
  const filePath = join(process.cwd(), '..', 'data', 'requests.json');
  const raw = readFileSync(filePath, 'utf-8');
  const data = JSON.parse(raw);
  return NextResponse.json(data);
}
