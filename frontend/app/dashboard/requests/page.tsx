'use client';

import { useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { AlertCircle, FileJson, Upload, X } from 'lucide-react';
import { useDropzone } from 'react-dropzone';

export default function RequestsUploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [processing, setProcessing] = useState(false);

  const onDrop = useCallback((acceptedFiles: File[]) => {
    setError(null);
    if (acceptedFiles.length > 0) {
      setFile(acceptedFiles[0]);
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/json': ['.json']
    },
    maxFiles: 1,
    maxSize: 5 * 1024 * 1024,
  });

  const handleAudit = async () => {
    if (!file) {
      setError('Please select a request JSON file');
      return;
    }

    setProcessing(true);
    try {
      const text = await file.text();
      // Validate it's parseable JSON
      const data = JSON.parse(text);
      
      // Store in session storage for the analysis page (simulate backend storage)
      sessionStorage.setItem('currentRequest', JSON.stringify(data));
      
      // Navigate to analysis page
      router.push('/dashboard/analysis');
    } catch (err) {
      setError('Failed to parse the uploaded file. Please ensure it is valid JSON.');
      setProcessing(false);
    }
  };

  return (
    <div className="py-8 w-full max-w-4xl mx-auto">
      <Card>
        <CardHeader>
          <CardTitle>Audit Procurement Request</CardTitle>
          <CardDescription>
            Upload a request.json file to automatically check for procurement rules violations.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-6">
            {!file ? (
              <div 
                {...getRootProps()} 
                className={`border-2 border-dashed rounded-lg p-10 text-center cursor-pointer transition-colors duration-300 ${isDragActive ? 'border-primary bg-primary/10' : 'border-gray-300 hover:border-primary/50'}`}
              >
                <input {...getInputProps()} />
                <Upload className="mx-auto h-12 w-12 text-muted-foreground mb-4" />
                <p className="text-sm font-medium">Drag and drop a .json file here, or click to select</p>
                <p className="text-xs text-muted-foreground mt-2">Max file size: 5MB</p>
              </div>
            ) : (
              <div className="flex items-center justify-between p-4 border rounded-lg bg-card">
                <div className="flex items-center gap-3">
                  <div className="h-10 w-10 rounded bg-primary/10 flex items-center justify-center">
                    <FileJson className="h-5 w-5 text-primary" />
                  </div>
                  <div>
                    <p className="text-sm font-medium">{file.name}</p>
                    <p className="text-xs text-muted-foreground">{(file.size / 1024).toFixed(2)} KB</p>
                  </div>
                </div>
                <Button variant="ghost" size="icon" onClick={() => setFile(null)}>
                  <X className="h-4 w-4" />
                </Button>
              </div>
            )}

            {error && (
              <div className="flex items-start gap-2 rounded-lg border border-destructive bg-destructive/10 p-4">
                <AlertCircle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <p className="text-sm font-medium text-destructive">Error</p>
                  <p className="text-sm text-destructive">{error}</p>
                </div>
              </div>
            )}

            <div className="flex gap-3 pt-4">
              <Button 
                onClick={handleAudit} 
                disabled={!file || processing}
                className="w-full sm:w-auto"
              >
                {processing ? 'Processing...' : 'Audit Request'}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
