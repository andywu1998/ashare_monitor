import { NextRequest, NextResponse } from 'next/server';
import { companyService } from '@/service';

/**
 * GET /api/companies - 获取所有公司
 */
export async function GET() {
  try {
    const companies = await companyService.getAllCompanies();
    return NextResponse.json({ success: true, data: companies });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '获取公司列表失败' },
      { status: 500 }
    );
  }
}

/**
 * POST /api/companies - 创建公司
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const company = await companyService.createCompany(body);
    return NextResponse.json({ success: true, data: company }, { status: 201 });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '创建公司失败' },
      { status: 500 }
    );
  }
}
