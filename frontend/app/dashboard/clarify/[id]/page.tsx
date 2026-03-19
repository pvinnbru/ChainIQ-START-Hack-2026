'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { AlertCircle, Lock } from 'lucide-react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

interface Escalation {
  id: string;
  type: string;
  message: string | null;
  status: string;
}

interface RequestDetail {
  id: string;
  plain_text: string;
  title: string | null;
  status: string;
  category_l1: string | null;
  category_l2: string | null;
  currency: string | null;
  budget_amount: number | null;
  quantity: number | null;
  unit_of_measure: string | null;
  required_by_date: string | null;
  preferred_supplier_mentioned: string | null;
  incumbent_supplier: string | null;
  escalations: Escalation[];
}

const CURRENCIES = ['EUR', 'CHF', 'USD'];
const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export default function ClarifyPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  const [request, setRequest] = useState<RequestDetail | null>(null);
  const [loading, setLoading] = useState(true);

  // Editable fields
  const [title, setTitle] = useState('');
  const [budgetAmount, setBudgetAmount] = useState('');
  const [currency, setCurrency] = useState('EUR');
  const [quantity, setQuantity] = useState('');
  const [unitOfMeasure, setUnitOfMeasure] = useState('');
  const [requiredByDate, setRequiredByDate] = useState('');
  const [preferredSupplier, setPreferredSupplier] = useState('');
  const [incumbentSupplier, setIncumbentSupplier] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    fetch(`${API}/requests/${id}`, { credentials: 'include' })
      .then((r) => r.json())
      .then((data: RequestDetail) => {
        setRequest(data);
        // Prefill fields from existing request
        setTitle(data.title ?? '');
        setBudgetAmount(data.budget_amount != null ? String(data.budget_amount) : '');
        setCurrency(data.currency ?? 'EUR');
        setQuantity(data.quantity != null ? String(data.quantity) : '');
        setUnitOfMeasure(data.unit_of_measure ?? '');
        setRequiredByDate(data.required_by_date ?? '');
        setPreferredSupplier(data.preferred_supplier_mentioned ?? '');
        setIncumbentSupplier(data.incumbent_supplier ?? '');
      })
      .catch(() => toast.error('Failed to load request'))
      .finally(() => setLoading(false));
  }, [id]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      const fields: Record<string, string | number | null> = {};
      if (title) fields.title = title;
      if (budgetAmount) fields.budget_amount = parseFloat(budgetAmount);
      if (currency) fields.currency = currency;
      if (quantity) fields.quantity = parseFloat(quantity);
      if (unitOfMeasure) fields.unit_of_measure = unitOfMeasure;
      if (requiredByDate) fields.required_by_date = requiredByDate;
      if (preferredSupplier) fields.preferred_supplier_mentioned = preferredSupplier;
      if (incumbentSupplier) fields.incumbent_supplier = incumbentSupplier;

      const res = await fetch(`${API}/requests/${id}/clarify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ fields }),
      });
      if (!res.ok) throw new Error('Failed to submit clarification');
      toast.success('Clarification submitted successfully!');
      router.push('/dashboard/cases');
    } catch {
      toast.error('Failed to submit. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-[60vh] items-center justify-center">
        <div className="h-10 w-10 animate-spin rounded-full border-b-2 border-primary" />
      </div>
    );
  }

  if (!request) {
    return (
      <div className="py-8 max-w-2xl mx-auto">
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Request not found</AlertTitle>
          <AlertDescription>This request could not be loaded.</AlertDescription>
        </Alert>
      </div>
    );
  }

  const pendingEscalations = request.escalations.filter(
    (e) => e.type === 'requester_clarification' && e.status === 'pending'
  );

  return (
    <div className="py-8 w-full max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Clarification Required</h1>
        <p className="text-muted-foreground mt-1">
          Please provide the missing information so your request can proceed.
        </p>
      </div>

      {/* Escalation messages */}
      {pendingEscalations.map((esc) => (
        <Alert key={esc.id} className="border-amber-300 bg-amber-50">
          <AlertCircle className="h-4 w-4 text-amber-600" />
          <AlertTitle className="text-amber-800">Action Required</AlertTitle>
          <AlertDescription className="text-amber-700">
            {esc.message ?? 'Additional information is needed to proceed with this request.'}
          </AlertDescription>
        </Alert>
      ))}

      {/* Original request (read-only) */}
      <Card className="bg-muted/30">
        <CardHeader className="pb-2">
          <div className="flex items-center gap-2">
            <Lock className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-sm text-muted-foreground">Original Request</CardTitle>
            <Badge variant="outline" className="text-xs ml-auto">
              {request.status.replace(/_/g, ' ')}
            </Badge>
          </div>
          {request.title && (
            <CardDescription className="text-base font-medium text-foreground pt-1">
              {request.title}
            </CardDescription>
          )}
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground italic leading-relaxed">
            "{request.plain_text}"
          </p>
        </CardContent>
      </Card>

      {/* Clarification form */}
      <form onSubmit={handleSubmit}>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Update Missing Information</CardTitle>
            <CardDescription>
              Fields are prefilled with what we already have — update anything that was unclear or missing.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="title">Title</Label>
                <Input
                  id="title"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="Short description"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="required-by">Required By Date</Label>
                <Input
                  id="required-by"
                  type="date"
                  value={requiredByDate}
                  onChange={(e) => setRequiredByDate(e.target.value)}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="budget">Budget Amount</Label>
                <Input
                  id="budget"
                  type="number"
                  min={0}
                  step="0.01"
                  placeholder="0.00"
                  value={budgetAmount}
                  onChange={(e) => setBudgetAmount(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Currency</Label>
                <Select value={currency} onValueChange={setCurrency}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CURRENCIES.map((c) => (
                      <SelectItem key={c} value={c}>{c}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="quantity">Quantity</Label>
                <Input
                  id="quantity"
                  type="number"
                  min={0}
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                  placeholder="0"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="unit">Unit of Measure</Label>
                <Input
                  id="unit"
                  value={unitOfMeasure}
                  onChange={(e) => setUnitOfMeasure(e.target.value)}
                  placeholder="e.g. units, consulting_day"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="preferred">Preferred Supplier</Label>
                <Input
                  id="preferred"
                  value={preferredSupplier}
                  onChange={(e) => setPreferredSupplier(e.target.value)}
                  placeholder="Optional"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="incumbent">Incumbent Supplier</Label>
                <Input
                  id="incumbent"
                  value={incumbentSupplier}
                  onChange={(e) => setIncumbentSupplier(e.target.value)}
                  placeholder="Optional"
                />
              </div>
            </div>
          </CardContent>
        </Card>

        <div className="flex justify-end gap-3 pt-4">
          <Button type="button" variant="outline" onClick={() => router.back()}>
            Cancel
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? 'Submitting…' : 'Submit Clarification'}
          </Button>
        </div>
      </form>
    </div>
  );
}
