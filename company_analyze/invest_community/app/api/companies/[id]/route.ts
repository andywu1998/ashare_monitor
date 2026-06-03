import { NextRequest, NextResponse } from 'next/server';
import { companyService } from '@/service';

/**
 * GET /api/companies/[id] - 获取公司详情
 */
export async function GET(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  try {
    const company = await companyService.getCompanyById(params.id);
    if (!company) {
      return NextResponse.json(
        { success: false, error: '公司不存在' },
        { status: 404 }
      );
    }
    return NextResponse.json({ success: true, data: company });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '获取公司详情失败' },
      { status: 500 }
    );
  }
}

/**
 * PUT /api/companies/[id] - 更新公司信息
 */
export async function PUT(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  try {
    const body = await request.json();
    const company = await companyService.updateCompany(params.id, body);
    return NextResponse.json({ success: true, data: company });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '更新公司信息失败' },
      { status: 500 }
    );
  }
}

/**
 * DELETE /api/companies/[id] - 删除公司
 */
export async function DELETE(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  try {
    await companyService.deleteCompany(params.id);
    return NextResponse.json({ success: true, message: '删除成功' });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '删除公司失败' },
      { status: 500 }
    );
  }
}
